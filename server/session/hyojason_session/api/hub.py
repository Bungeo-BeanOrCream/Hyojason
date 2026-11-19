"""SessionHub — 페어별 통화(SessionControl)와 역할별 WS 연결을 묶는 제어 서버의 중심.

- 통화: POST /v1/calls에서 SessionControl을 만들고 trace_id를 발급한다(결정 메모 6).
  N-22 장부와 N-23 종료 기록은 페어 단위로 공유해 통화가 바뀌어도 재접속(N-13)이 이전 도움을 안다.
- 수신: 앱 이벤트 → SessionControl.apply, help.complete/stop → submit_command(N-22),
  서버 내부 이벤트(B-02 발화, C의 판단, N-03 통화 상태) → apply, N-18 도착 → submit_arrival.
- 송신: N-11 효과(앱 대상만), N-23 help.ended, 상태가 바뀔 때마다 N-21 역할 화면(help.view).
- 지원 대상(#6, 기본 부모)의 상대 기기에는 wire.COUNTERPART_DELIVERABLE에 있는 이벤트만 나간다.
- trace_id는 도움마다 새로 쓴다(새 코드 승인으로 새 도움이 열리면 바뀜).
- B-05(C 판단): agent를 주면 N-11 적용·전달 뒤 agent_bridge가 C를 부르고 결과를 apply_internal로 다시 넣는다.
상태는 메모리에만 있다(DB 없음). 로그를 남기지 않는다.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ..common.decisions import ArrivalDecision
from ..common.enums import ArrivalState, Role, Source
from ..common.events import ControlEvent
from ..common.result import Basis, NodeResult, new_id
from ..common.serialize import to_jsonable
from ..common.types import ActorContext, HelpSnapshot
from ..completion import CompletionLedger
from ..control import SessionControl, Update
from ..help_state import Actor, Event
from ..navigation import NavigationCatalog
from ..ports import HelpScopedStore, HelpTombstone, PairDirectory
from ..purge import TombstoneRegistry
from ..reconnect import ReconnectRequest, reconcile_reconnect
from ..role_view import RoleViewInput, project_role_view
from ..views import RoleSnapshot
from . import wire
from .wire import WireError


@dataclass(eq=False)
class Connection:
    actor: ActorContext
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class _PairSink:
    """SessionControl(N-23)이 help.ended를 보내는 EventSink."""

    def __init__(self, hub: SessionHub, pair_id: str) -> None:
        self._hub, self._pair_id = hub, pair_id

    def send(self, role: Role, event: ControlEvent) -> bool:
        return self._hub.deliver(self._pair_id, role, event)


class _PairReader:
    """N-13이 읽는 HelpStateReader. 페어의 통화들을 최신부터 본다."""

    def __init__(self, hub: SessionHub, pair_id: str) -> None:
        self._hub, self._pair_id = hub, pair_id

    def _controls(self) -> list[SessionControl]:
        return self._hub.controls_of(self._pair_id)

    def latest_help_id(self, pair_id: str) -> str | None:
        controls = self._controls()
        return controls[0].latest_help_id(pair_id) if controls else None

    def get_snapshot(self, help_id: str) -> HelpSnapshot | None:
        for control in self._controls():
            snapshot = control.get_snapshot(help_id)
            if snapshot is not None:
                return snapshot
        return None

    def get_tombstone(self, help_id: str) -> HelpTombstone | None:
        return self._hub.tombstones_of(self._pair_id).get(help_id)

    def arrival_state(self, help_id: str) -> ArrivalState:
        for control in self._controls():
            if control.snapshot.help_id == help_id:
                return control.arrival_state(help_id)
        return ArrivalState.NONE

    def observation_paused(self, help_id: str) -> bool:
        for control in self._controls():
            if control.snapshot.help_id == help_id:
                return control.observation_paused(help_id)
        return False


class SessionHub:
    def __init__(self, *, catalog: NavigationCatalog, approved_slots: Mapping[str, Callable],
                 pairs: PairDirectory, pairing, source: Source = Source.FIXTURE,
                 result_stores: Callable[[], Sequence[HelpScopedStore]] = tuple,
                 agent=None) -> None:
        """agent: B-05 연결(agent_bridge.AgentBridge). 없으면 C 판단 없이 앱·내부 이벤트만 처리한다."""
        self._catalog = catalog
        self._slots = dict(approved_slots)
        self._pairs = pairs
        self._pairing = pairing
        self._source = Source(source)
        self._result_stores = result_stores
        self._agent = agent
        self._lock = threading.RLock()
        self._calls: dict[str, SessionControl] = {}
        self._call_traces: dict[str, str] = {}  # call_id → 처음 trace_id (B 재등록 확인용)
        self._pending_assisted: dict[str, Role] = {}  # B 발신 중인 페어의 첫 도움 지원 대상
        self._pair_calls: dict[str, list[str]] = {}
        self._ledgers: dict[str, CompletionLedger] = {}
        self._tombstones: dict[str, TombstoneRegistry] = {}
        self._connections: dict[tuple[str, Role], Connection] = {}

    # --- 연결 -----------------------------------------------------------------
    def connect(self, actor: ActorContext, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> Connection:
        """역할당 연결 하나. 새로 붙으면 이전 연결 대신 새 연결로 보낸다."""
        conn = Connection(actor, queue, loop)
        with self._lock:
            self._connections[(actor.pair_id, actor.role)] = conn
        return conn

    def disconnect(self, conn: Connection) -> None:
        with self._lock:
            key = (conn.actor.pair_id, conn.actor.role)
            if self._connections.get(key) is conn:
                del self._connections[key]

    def assisted_of(self, pair_id: str) -> Role:
        control = self.current_control(pair_id)
        return Role(control.snapshot.assisted_role) if control is not None else Role.PARENT

    def deliver(self, pair_id: str, role: Role, event: ControlEvent) -> bool:
        if role is not self.assisted_of(pair_id) and event.type not in wire.COUNTERPART_DELIVERABLE:
            return False
        with self._lock:
            conn = self._connections.get((pair_id, role))
        if conn is None:
            return False
        self._put(conn, to_jsonable(event))
        return True

    @staticmethod
    def _put(conn: Connection, message: dict) -> None:
        try:
            same_loop = asyncio.get_running_loop() is conn.loop
        except RuntimeError:
            same_loop = False
        if same_loop:
            conn.queue.put_nowait(message)
        else:
            conn.loop.call_soon_threadsafe(conn.queue.put_nowait, message)

    # --- 통화 -----------------------------------------------------------------
    def controls_of(self, pair_id: str) -> list[SessionControl]:
        with self._lock:
            return [self._calls[c] for c in reversed(self._pair_calls.get(pair_id, []))]

    def current_control(self, pair_id: str) -> SessionControl | None:
        controls = self.controls_of(pair_id)
        return controls[0] if controls else None

    def tombstones_of(self, pair_id: str) -> TombstoneRegistry:
        with self._lock:
            return self._tombstones.setdefault(pair_id, TombstoneRegistry())

    def _ledger_of(self, pair_id: str) -> CompletionLedger:
        with self._lock:
            return self._ledgers.setdefault(pair_id, CompletionLedger())

    def control_for(self, call_id: str, actor: ActorContext | None = None) -> SessionControl:
        with self._lock:
            control = self._calls.get(call_id)
        if control is None:
            raise WireError("NOT_FOUND")
        if actor is not None and control.snapshot.pair_id != actor.pair_id:
            raise WireError("FORBIDDEN", "UNAUTHORIZED")
        return control

    def create_call(self, actor: ActorContext, assisted_role: Role = Role.PARENT) -> SessionControl:
        """D가 통화 ID를 발급하는 발신 (MOCK N-03). 부모·자녀 모두 걸 수 있다(#6·#10). assisted_role은 첫 도움의 지원 대상."""
        return self._open_call(actor.pair_id, new_id("call"), new_id("trace"), actor.role, assisted_role)

    def register_call(self, pair_id: str, call_id: str, trace_id: str, inviter: Role,
                      assisted_role: Role | None = None) -> SessionControl:
        """B가 발급한 통화를 같은 call_id·trace_id로 D에 등록한다 (#23 C 제안, #10: B는 통화 ID, D는 도움 세션).

        같은 call_id를 다시 등록하면 페어와 처음 trace_id가 같을 때만 그 통화를 돌려준다(B 재전송).
        assisted_role을 주지 않으면 발신 경로가 미리 정해 둔 값(start_call), 그것도 없으면 부모다.
        """
        with self._lock:
            existing = self._calls.get(call_id)
            if existing is not None:
                if existing.snapshot.pair_id != pair_id or self._call_traces.get(call_id) != trace_id:
                    raise WireError("CONFLICT", "DUPLICATE")
                return existing
            assisted = assisted_role or self._pending_assisted.pop(pair_id, Role.PARENT)
        return self._open_call(pair_id, call_id, trace_id, Role(inviter), assisted)

    def _open_call(self, pair: str, call_id: str, trace_id: str, inviter: Role,
                   assisted_role: Role) -> SessionControl:
        with self._lock:
            current = self.current_control(pair)
            if current is not None and current.snapshot.call_state != "ENDED":
                raise WireError("CONFLICT", "CALL_ALREADY_OPEN")
            stores = list(self._result_stores())
            if self._agent is not None:
                stores.append(self._agent.results)  # N-23이 도움 종료 때 B-05 기록도 지운다
            control = SessionControl(
                pair, call_id, new_id("help"), catalog=self._catalog, approved_slots=self._slots,
                source=self._source.value, events=_PairSink(self, pair), ledger=self._ledger_of(pair),
                tombstones=self.tombstones_of(pair), result_stores=stores,
                trace_id=trace_id, assisted_role=assisted_role,
            )
            self._calls[call_id] = control
            self._call_traces[call_id] = trace_id
            self._pair_calls.setdefault(pair, []).append(call_id)
        update = self._apply_or_raise(control, "call.invite", inviter.value, dispatch=False)
        s = control.snapshot
        # 받는 쪽 앱은 수신 알림을 먼저 받고, 그 뒤 역할 화면을 받는다
        callee = Role.CHILD if inviter is Role.PARENT else Role.PARENT
        self.deliver(pair, callee, wire.control_event("call.invite", pair, s.call_id, s.help_id, {}))
        self._dispatch(control, update)
        self.sync_consent(pair)  # 통화 전에 B(N-02)에서 받은 동의를 이 통화의 N-11에 반영
        return control

    def accept_call(self, actor: ActorContext, call_id: str) -> SessionControl:
        """수락은 건 쪽의 상대만 (N-11이 확인한다)."""
        control = self.control_for(call_id, actor)
        self._apply_or_raise(control, "call.accepted", actor.role.value)
        if self._pairing.rtc_connected_on_accept():  # MOCK N-03: LiveKit 참가 확인 대신
            self._apply_or_raise(control, "call.connected", "SERVER")
        return control

    # --- B 음성 서비스 (#10: B는 판단 함수, D는 라우트·도움 세션) ----------------------
    @property
    def voice_backed(self) -> bool:
        """B의 service가 통화·동의를 맡는지. 아니면 MOCK(MockPairingService)이고 D가 통화 ID를 발급한다."""
        return hasattr(self._pairing, "start_call")

    def start_call(self, actor: ActorContext, assisted_role: Role = Role.PARENT) -> tuple[SessionControl, object]:
        """POST /v1/calls → (통화, B N-03 결과 또는 None).

        B가 있으면 B가 call_id·trace_id를 만들고 call.invite를 보내 register_call로 등록된다.
        """
        if not self.voice_backed:
            return self.create_call(actor, assisted_role), None
        with self._lock:
            self._pending_assisted[actor.pair_id] = assisted_role
        try:
            result = self._pairing.start_call(actor)
        finally:
            with self._lock:
                self._pending_assisted.pop(actor.pair_id, None)
        return self.control_for(result.data.call_id, actor), result

    def accept(self, actor: ActorContext, call_id: str) -> tuple[SessionControl, object]:
        """POST /v1/calls/{id}/accept. B가 있으면 B N-03이 수락을 판단하고 call.accepted로 알려 온다."""
        if not self.voice_backed:
            return self.accept_call(actor, call_id), None
        self.control_for(call_id, actor)
        result = self._pairing.accept_call(actor, call_id)
        return self.control_for(call_id, actor), result

    def end_call(self, actor: ActorContext, call_id: str) -> tuple[SessionControl, object]:
        """POST /v1/calls/{id}/end. B가 있으면 B N-03 → call.ended, 없으면 MOCK으로 바로 call.ended."""
        control = self.control_for(call_id, actor)
        if self.voice_backed:
            return control, self._pairing.end_call(actor, call_id)
        if control.snapshot.call_state != "ENDED":
            self._apply_or_raise(control, "call.ended", "SERVER")
        return control, None

    def current_call(self, actor: ActorContext) -> dict | None:
        """GET /v1/calls/current (B 앱의 폴링·재연결 복구). B가 있으면 B 값, 없으면 D 통화."""
        if self.voice_backed:
            return self._pairing.current_call(actor)
        control = self.current_control(actor.pair_id)
        if control is None or control.snapshot.call_state == "ENDED":
            return None
        s = control.snapshot
        return {"call_id": s.call_id, "call_state": s.call_state, "help_id": s.help_id}

    def on_voice_event(self, trace_id: str, event: Event) -> NodeResult | None:
        """B service.set_event_sink 대상: (trace_id, Event)로 오는 call.*·speech.final을 같은 통화에 적용한다.

        B의 통화 처리 중에 불리므로 예외를 올리지 않고 결과(또는 None)만 돌려준다. help_id는 D가 붙인다.
        """
        payload = dict(event.payload or {})
        try:
            if event.type == "call.invite":
                control = self.register_call(event.pair_id, event.call_id, trace_id, Role(payload["role"]))
                return None
            control = self.control_for(event.call_id)
            if control.snapshot.pair_id != event.pair_id:
                return None
            if event.type == "call.accepted":
                return self._apply(control, "call.accepted", Role(payload["role"]).value).state
            if event.type in ("call.connected", "call.ended", "speech.final"):
                if event.type == "call.ended" and control.snapshot.call_state == "ENDED":
                    return None
                return self._apply(control, event.type, "SERVER", payload, event_id=event.event_id).state
        except (WireError, KeyError, ValueError):
            return None
        return None

    def sync_consent(self, pair_id: str) -> None:
        """B N-02의 역할별 분석 동의를 진행 중 통화의 N-11에 맞춘다. 같은 값이면 N-11이 무시한다.

        MOCK 모드에는 consenting_roles가 없고, 앱이 WS consent.updated로 직접 보낸다.
        """
        roles_of = getattr(self._pairing, "consenting_roles", None)
        control = self.current_control(pair_id)
        if roles_of is None or control is None or control.snapshot.call_state == "ENDED":
            return
        roles = roles_of(pair_id)
        for role in (Role.PARENT, Role.CHILD):
            self._apply(control, "consent.updated", role.value, {"purpose": "analysis", "enabled": role in roles})

    # --- 수신 -----------------------------------------------------------------
    def handle_client(self, actor: ActorContext, message: object) -> dict:
        """WS로 받은 앱 메시지 하나를 처리하고 보낸 쪽에 줄 응답(ack/rejected/pong)을 돌려준다."""
        event_id = message.get("event_id") if isinstance(message, dict) else None
        if isinstance(message, dict) and message.get("type") == wire.PING:
            return {"v": 1, "type": wire.PONG, "event_id": event_id}
        try:
            env = wire.parse_envelope(message, actor)
            control = self.control_for(env["call_id"], actor)
            if control is not self.current_control(actor.pair_id):
                raise WireError("CONFLICT", "STALE_BASIS")  # 지난 통화로 온 이벤트
            wire.check_sender(env, actor, Role(control.snapshot.assisted_role))
            if env["type"] == "consent.updated" and self.voice_backed:
                raise WireError("FORBIDDEN", "UNAUTHORIZED")  # B N-02가 동의를 맡는다: POST /v1/consent
            if env["type"] in wire.COMMANDS:
                if env["help_id"] is None:
                    raise WireError("INVALID_EVENT")
                resolution = control.submit_command(wire.COMMANDS[env["type"]], actor, env["help_id"])
                node = resolution.completion
                if resolution.update is not None:
                    self._dispatch(control, resolution.update)
                    if resolution.update.state.status == "error":
                        node = resolution.update.state
            else:
                update = self._apply(control, env["type"], actor.role.value, env["payload"],
                                     event_id=env["event_id"], help_id=env["help_id"])
                node = update.state
        except WireError as error:
            return wire.rejected(event_id, error.api_code, error.node_code)
        if node.status == "error":
            code = node.diagnostics.terminal_error.code
            return wire.rejected(event_id, wire.api_code_for(code), code)
        return wire.ack(event_id, node.outcome)

    def apply_server_event(self, call_id: str, body: Mapping) -> NodeResult:
        """B-02 발화, C의 N-15·N-27·N-28 결과, N-03 통화 상태처럼 서버 안쪽에서 오는 이벤트."""
        control = self.control_for(call_id)
        kind = body.get("type")
        if not isinstance(kind, str) or kind in wire.COMMANDS or kind == "help.end" or kind.startswith("arrival."):
            raise WireError("FORBIDDEN", "UNAUTHORIZED")  # 종료는 N-22, 도착은 arrivals 경로로만
        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            raise WireError("INVALID_EVENT")
        return self._apply(control, kind, "SERVER", payload, event_id=body.get("event_id"),
                           help_id=body.get("help_id", control.snapshot.help_id),
                           basis=_basis_from(body.get("basis"))).state

    def submit_arrival(self, call_id: str, body: Mapping) -> NodeResult:
        control = self.control_for(call_id)
        basis = _basis_from(body.get("basis")) or control.snapshot.basis
        codes = body.get("evidence_codes", [])
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise WireError("INVALID_EVENT")
        decision = ArrivalDecision(
            arrival_id=body.get("arrival_id"), arrived=body.get("arrived"),
            goal_revision=body.get("goal_revision", basis.goal_revision),
            evidence_codes=tuple(codes), basis=basis,
        )
        resolution = control.submit_arrival(decision)
        if resolution.update is not None:
            self._dispatch(control, resolution.update, arrival_id=resolution.completion.data.arrival_id)
            if resolution.update.state.status == "error":
                return resolution.update.state
        return resolution.completion

    def reconnect(self, actor: ActorContext, help_id: str | None, last_event_seq: int,
                  request_id: str) -> NodeResult:
        control = self.current_control(actor.pair_id)
        trace = control.trace_id if control is not None and control.trace_id else new_id("trace")
        return reconcile_reconnect(
            ReconnectRequest(actor.pair_id, help_id, actor.role, last_event_seq, request_id),
            actor=actor, pairs=self._pairs, store=_PairReader(self, actor.pair_id),
            trace_id=trace, source=self._source,
        )

    def claim(self, actor: ActorContext) -> dict:
        """MOCK N-01 승인. B가 있으면 앱 경로가 B의 claim(코드·새 토큰)을 직접 부른다."""
        return self._pairing.claim(actor)

    @property
    def voice(self):
        """B 음성 서비스 어댑터 (voice_backed일 때). 페어·동의·웹훅·dev 경로가 쓴다."""
        return self._pairing

    def rtc_token(self, actor: ActorContext, call_id: str) -> dict:
        self.control_for(call_id, actor)
        return self._pairing.rtc_token(actor, call_id)

    def apply_internal(self, control: SessionControl, kind: str, payload: Mapping, basis: Basis) -> Update:
        """B-05 결과(지시·목표 초안, 위치, 수행 판정)를 서버 이벤트로 N-11에 넣는다. 판단한 범위(basis)로 검사한다."""
        return self._apply(control, kind, "SERVER", payload, basis=basis)

    # --- 내부 -----------------------------------------------------------------
    def _apply(self, control: SessionControl, kind: str, role: str, payload: Mapping | None = None, *,
               event_id: str | None = None, help_id: str | None = None,
               basis: Basis | None = None, dispatch: bool = True) -> Update:
        s = control.snapshot
        event = Event(kind, event_id or new_id("evt"), s.pair_id, s.call_id,
                      help_id if help_id is not None else s.help_id, dict(payload or {}))
        update = control.apply(event, Actor(role, s.pair_id, s.call_id), basis or s.basis)
        if dispatch and update.state.status == "success" and update.state.outcome != "duplicate_ignored":
            self._dispatch(control, update)
            if self._agent is not None:
                # 앱 전달(instruction.accepted 등) 뒤에 C 판단을 부른다: 부모 t0가 위치 탐색을 기다리지 않는다
                self._agent.after_apply(self, control, kind, payload or {}, s, update)
        return update

    def _apply_or_raise(self, control: SessionControl, kind: str, role: str, *,
                        dispatch: bool = True) -> Update:
        update = self._apply(control, kind, role, dispatch=dispatch)
        if update.state.status == "error":
            code = update.state.diagnostics.terminal_error.code
            raise WireError(wire.api_code_for(code), code)
        return update

    def _dispatch(self, control: SessionControl, update: Update, *, arrival_id: str | None = None) -> None:
        snapshot = control.snapshot
        for role, event in wire.effect_events(update, snapshot, arrival_id=arrival_id):
            self.deliver(snapshot.pair_id, role, event)
        self._push_views(control)

    def _push_views(self, control: SessionControl) -> None:
        """N-21 역할 화면을 연결된 역할에게 다시 보낸다 (B-07 정책 1)."""
        snapshot = control.snapshot
        shared = control.get_snapshot(snapshot.help_id)
        if shared is None:
            return
        for role in (Role.PARENT, Role.CHILD):
            with self._lock:
                conn = self._connections.get((snapshot.pair_id, role))
            if conn is None:
                continue
            view = project_role_view(
                RoleViewInput(shared, role, control.arrival_state(shared.help_id), True),
                actor=conn.actor, latest_help_id=shared.help_id,
                trace_id=control.trace_id or new_id("trace"), source=self._source,
            )
            if view.data is None:
                continue
            payload = to_jsonable(RoleSnapshot(role, shared.pair_id, shared.call_id, shared.help_id,
                                               view.data.parent_view, view.data.child_view))
            self.deliver(snapshot.pair_id, role, wire.control_event(
                wire.VIEW_EVENT, shared.pair_id, shared.call_id, shared.help_id, payload))


def _basis_from(value: object) -> Basis | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not set(value) <= {f for f in Basis.__dataclass_fields__}:
        raise WireError("INVALID_EVENT")
    return Basis(**value)
