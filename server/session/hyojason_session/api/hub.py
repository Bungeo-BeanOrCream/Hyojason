"""SessionHub — 페어별 통화(SessionControl)와 역할별 WS 연결을 묶는 제어 서버의 중심.

- 통화: POST /v1/calls에서 SessionControl을 만들고 trace_id를 발급한다(결정 메모 6).
  N-22 장부와 N-23 종료 기록은 페어 단위로 공유해 통화가 바뀌어도 재접속(N-13)이 이전 도움을 안다.
- 수신: 앱 이벤트 → SessionControl.apply, help.complete/stop → submit_command(N-22),
  서버 내부 이벤트(B-02 발화, C의 판단, N-03 통화 상태) → apply, N-18 도착 → submit_arrival.
- 송신: N-11 효과(앱 대상만), N-23 help.ended, 상태가 바뀔 때마다 N-21 역할 화면(help.view).
- 지원 대상(#6, 기본 부모)의 상대 기기에는 wire.COUNTERPART_DELIVERABLE에 있는 이벤트만 나간다.
- trace_id는 도움마다 새로 쓴다(새 코드 승인으로 새 도움이 열리면 바뀜).
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
                 result_stores: Callable[[], Sequence[HelpScopedStore]] = tuple) -> None:
        self._catalog = catalog
        self._slots = dict(approved_slots)
        self._pairs = pairs
        self._pairing = pairing
        self._source = Source(source)
        self._result_stores = result_stores
        self._lock = threading.RLock()
        self._calls: dict[str, SessionControl] = {}
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
        """부모 발신(자녀 발신 P0 여부는 #6 팀 결정). assisted_role은 첫 도움의 지원 대상."""
        if actor.role is not Role.PARENT:
            raise WireError("FORBIDDEN", "UNAUTHORIZED")
        with self._lock:
            current = self.current_control(actor.pair_id)
            if current is not None and current.snapshot.call_state != "ENDED":
                raise WireError("CONFLICT", "CALL_ALREADY_OPEN")
            pair = actor.pair_id
            control = SessionControl(
                pair, new_id("call"), new_id("help"), catalog=self._catalog, approved_slots=self._slots,
                source=self._source.value, events=_PairSink(self, pair), ledger=self._ledger_of(pair),
                tombstones=self.tombstones_of(pair), result_stores=self._result_stores(),
                trace_id=new_id("trace"), assisted_role=assisted_role,
            )
            call_id = control.snapshot.call_id
            self._calls[call_id] = control
            self._pair_calls.setdefault(pair, []).append(call_id)
        update = self._apply_or_raise(control, "call.invite", "PARENT", dispatch=False)
        s = control.snapshot
        # 자녀 앱은 수신 알림을 먼저 받고, 그 뒤 역할 화면을 받는다
        self.deliver(pair, Role.CHILD, wire.control_event("call.invite", pair, s.call_id, s.help_id, {}))
        self._dispatch(control, update)
        return control

    def accept_call(self, actor: ActorContext, call_id: str) -> SessionControl:
        control = self.control_for(call_id, actor)
        if actor.role is not Role.CHILD:
            raise WireError("FORBIDDEN", "UNAUTHORIZED")
        self._apply_or_raise(control, "call.accepted", "CHILD")
        if self._pairing.rtc_connected_on_accept():  # MOCK N-03: LiveKit 참가 확인 대신
            self._apply_or_raise(control, "call.connected", "SERVER")
        return control

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
        return self._pairing.claim(actor)

    def rtc_token(self, actor: ActorContext, call_id: str) -> dict:
        self.control_for(call_id, actor)
        return self._pairing.rtc_token(actor, call_id)

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
