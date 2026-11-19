"""B-05 연결 — 세션 서버(D)가 C의 판단 흐름(server/agent B05Workflow)을 부르고 결과를 다시 넣는다.

D → C → D → 지원 대상 기기. 근거: 이슈 #14 다음 계획 3, server/agent/README.md '연결', docs/contracts.md 이벤트 표.

| D에서 일어난 일 | B-05 이벤트 | 결과 처리 |
|---|---|---|
| speech.final 적용 (N-11 turn.interpret 효과) | speech.final | 지시 초안 → instruction.drafted (목표 모델보다 먼저), 목표 초안 → goal.drafted |
| instruction.accepted 효과 | instruction.accepted | TargetDecision → target.located |
| 지원 대상의 screen.observed | N-12가 전이를 기록했으면 observation.transition, 아니면 screen.observed | target.located · action.assessed, recovery.ready, guide.proposed, 도착 추정 → N-22 |
| call.ended (목표가 남아 도움 계속) | call.ended | B-05가 새 안정 화면을 기다린다 |

- B-05에는 같은 순간의 snapshot·방문 이력·처리권을 한 번에 넘긴다 (SessionControl.agent_view → from_session).
- 오래된 결과는 두 번 거른다: B-05의 read_current와 N-11의 STALE_BASIS. target.located·action.assessed는
  N-11이 받아들였을 때만 앱으로 보낸다. recovery.ready·guide.proposed는 N-11 상태를 바꾸지 않으므로
  판단한 범위가 아직 최신인지 확인한 뒤 보낸다.
- 결과 이벤트는 지원 대상 기기에만 간다. 상대 기기 전달은 hub.deliver가 막는다 (N-21).
- 한 작업 안에서 생긴 다음 작업은 대기열 뒤에 붙인다 (재귀 호출 없음). threaded=True면 작업 스레드
  하나가 차례로 처리해 모델 호출이 WS 이벤트 루프를 막지 않는다. 테스트는 threaded=False(그 자리에서 처리).
- B-05 실행 기록은 도움 단위 저장소(INFERENCE_RESULTS)에 두고 N-23이 도움이 끝날 때 지운다.
  발화 원문은 C에만 넘기고 저장하지 않는다.

모델: with_zoro_from_env()는 L40S의 실제 Zoro-1(#23)을 부른다. MOCK fixture_zoro()는 질문의 첫 선택지(또는
prefer에 있는 선택지)를 0.95로 고른다. 실제 모델이어도 통화·발화·화면이 합성이면 결과 출처는 FIXTURE다
(모델 호출 자체는 결과의 model_revision으로 구분한다).
C 코드(server/agent)는 고치지 않고 import만 한다. 저장소 루트가 sys.path에 있어야 한다 (pyproject pythonpath).
"""

from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields

from server.agent._execution import ExecutionContext
from server.agent.b05_workflow import AgentSnapshot, B05Input, B05Workflow
from server.agent.infer_zoro_choice import InferZoroChoice
from server.agent.session_adapter import from_session
from server.contracts import FinalUtterance
from server.contracts import ScreenState as AgentScreen

from ..common.enums import CleanupScope, Role, Source
from ..common.result import Basis, new_id
from ..common.serialize import to_jsonable
from ..navigation import InstructionTrace, TrailEntry
from . import wire

# 앱으로 직접 보내는 B-05 결과 이벤트 (docs/contracts.md). 지원 대상 기기만 받는다.
TARGET_LOCATED, ACTION_ASSESSED = "target.located", "action.assessed"
RECOVERY_READY, GUIDE_PROPOSED = "recovery.ready", "guide.proposed"
RESULT_EVENTS = frozenset({TARGET_LOCATED, ACTION_ASSESSED, RECOVERY_READY, GUIDE_PROPOSED})
# 실제 Zoro-1에 필요한 실행 환경 값 (C의 InferZoroChoice.from_environment와 같은 이름)
ZORO_REQUIRED = ("ZORO_DECIDE_URL", "ZORO_MODEL_REVISION")


def fixture_zoro(*, prefer: Sequence[str] = (), score: float = 0.95) -> InferZoroChoice:
    """MOCK N-14: 실제 Zoro-1 대신 정해진 규칙으로 고르는 모델. C의 InferZoroChoice에 가짜 전송만 끼운다."""

    def transport(payload, timeout):
        criteria = list(payload["request"]["questions"]["decision"]["criteria"])
        selected = next((choice for choice in prefer if choice in criteria), criteria[0])
        rest = (1 - score) / (len(criteria) - 1)
        return {"answers": {"decision": {"type": "choice", "choice": selected,
                                         "probabilities": {c: score if c == selected else rest for c in criteria}}}}

    return InferZoroChoice(model_revision="fixture-zoro", transport=transport)


@dataclass(frozen=True)
class AgentRun:
    """B-05 호출 한 번과 그 결과를 D에 넣은 기록. applied는 (이벤트, outcome 또는 오류 코드)."""

    help_id: str
    event: str
    status: str | None
    outcome: str | None
    error: str | None
    applied: tuple[tuple[str, str | None], ...]


class InferenceResults:
    """INFERENCE_RESULTS 범위: 도움별 B-05 실행 기록. N-23이 종료 때 purge한다(멱등)."""

    scope = CleanupScope.INFERENCE_RESULTS

    def __init__(self, limit: int = 200) -> None:
        self._limit = limit
        self._runs: dict[str, deque[AgentRun]] = {}
        self._lock = threading.Lock()

    def add(self, run: AgentRun) -> None:
        with self._lock:
            self._runs.setdefault(run.help_id, deque(maxlen=self._limit)).append(run)

    def of(self, help_id: str) -> tuple[AgentRun, ...]:
        with self._lock:
            return tuple(self._runs.get(help_id, ()))

    def purge(self, help_id: str) -> None:
        with self._lock:
            self._runs.pop(help_id, None)


@dataclass(frozen=True)
class _Job:
    event: str
    request: B05Input
    basis: Basis  # B-05에 넘긴 순간의 D 범위. 결과는 이 범위로 N-11에 넣는다.


def _screen(screen) -> AgentScreen:
    return AgentScreen(projection_epoch=screen.projection_epoch, screen_revision=screen.screen_revision,
                       screen_kind=screen.screen_kind, target_ids=tuple(screen.target_ids),
                       evidence_codes=tuple(screen.evidence_codes),
                       observation_health=screen.observation_health)


def _trail(trail) -> dict:
    """D의 N-12 NavigationTrail → C의 from_session이 읽는 형태 (화면 원본·좌표 없음)."""

    def ref(screen):
        return {"projection_epoch": screen.projection_epoch, "screen_revision": screen.screen_revision,
                "screen_kind": screen.screen_kind}

    return {
        "help_id": trail.help_id,
        "entries": [{"from_screen": ref(r.from_screen), "to_screen": ref(r.to_screen),
                     "projection_epoch": r.projection_epoch, "evidence_codes": r.evidence_codes,
                     "reversible": r.reversible, "observed_seq": r.observed_seq}
                    for r in trail.records if isinstance(r, TrailEntry)],
        "directives": [{"instruction_id": r.instruction_id, "instruction_revision": r.instruction_revision,
                        "target_id": r.target_id, "origin_screen_revision": r.origin_screen_revision,
                        "evidence_codes": r.evidence_codes, "outcome": r.outcome}
                       for r in trail.records if isinstance(r, InstructionTrace)],
        "gap_after_seq": trail.gap_after_seq,
        "history_truncated": trail.history_truncated,
    }


def _agent_snapshot(control) -> tuple[object, AgentSnapshot]:
    snapshot, trail, allowed = control.agent_view()
    return snapshot, from_session(snapshot, _trail(trail), processing_allowed=allowed)


def _basis_dict(basis: Basis) -> dict:
    return {f.name: getattr(basis, f.name) for f in fields(basis)}


def _same(a: Basis, b: Basis, names: Sequence[str]) -> bool:
    return all(getattr(a, name) == getattr(b, name) for name in names)


class AgentBridge:
    """SessionHub가 N-11 이벤트를 적용한 뒤 after_apply로 부른다."""

    def __init__(self, workflow: B05Workflow, *, source: Source | str = Source.FIXTURE,
                 threaded: bool = False, results: InferenceResults | None = None) -> None:
        self._workflow = workflow
        self._source = Source(source)
        self.results = results or InferenceResults()
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._draining = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="b05") if threaded else None
        self._idle = threading.Condition()
        self._pending = 0  # threaded: 아직 끝나지 않은 작업 수 (작업 중에 생긴 작업 포함)

    @classmethod
    def with_fixture_model(cls, *, prefer: Sequence[str] = (), **kwargs) -> AgentBridge:
        return cls(B05Workflow(infer_zoro_choice=fixture_zoro(prefer=prefer).run),
                   **{"source": Source.FIXTURE, **kwargs})

    @classmethod
    def with_zoro_from_env(cls, env: Mapping[str, str] | None = None, **kwargs) -> AgentBridge:
        """실제 Zoro-1: ZORO_DECIDE_URL·ZORO_MODEL_REVISION(필수), ZORO_DECIDE_TOKEN(서빙 프록시가 요구할 때).

        값이 없으면 C의 N-14가 모든 호출을 DEPENDENCY_UNAVAILABLE로 끝내 안내가 조용히 사라지므로,
        서버를 띄울 때 바로 알린다. 값은 저장소가 아니라 실행 환경에서만 준다 (server/agent/README.md).
        """
        env = os.environ if env is None else env
        missing = [name for name in ZORO_REQUIRED if not env.get(name)]
        if missing:
            raise RuntimeError(f"set {', '.join(missing)} for HYOJASON_AGENT=zoro (server/agent/README.md)")
        model = InferZoroChoice(endpoint=env["ZORO_DECIDE_URL"], model_revision=env["ZORO_MODEL_REVISION"],
                                bearer_token=env.get("ZORO_DECIDE_TOKEN") or None)
        return cls(B05Workflow(infer_zoro_choice=model.run), **kwargs)

    # --- 입구 ---------------------------------------------------------------------
    def after_apply(self, hub, control, kind: str, payload: Mapping, previous, update) -> None:
        """N-11이 이벤트를 받아들이고 앱 전달까지 끝난 뒤. B-05가 할 일이 있으면 대기열에 넣는다."""
        state = update.state
        if state.status == "error" or state.outcome == "duplicate_ignored":
            return
        effects = {effect.kind for effect in state.data.side_effects}
        current = state.data.snapshot
        events: list[tuple[str, dict]] = []
        if "turn.interpret" in effects:
            events.append(("speech.final", {"speech": self._speech(current, payload)}))
        if "instruction.accepted" in effects:
            events.append(("instruction.accepted", {}))
        if kind == "screen.observed" and current.screen is not None and current.observation_health == "ACTIVE":
            nav = update.navigation
            verified = (nav is not None and nav.status != "error" and nav.data is not None
                        and nav.data.recorded_edge is not None)
            if verified and previous.screen is not None and previous.help_id == current.help_id:
                events.append(("observation.transition", {"before": _screen(previous.screen)}))
            else:
                events.append(("screen.observed", {}))
        if kind == "call.ended" and state.outcome == "call_ended_help_active":
            events.append(("call.ended", {}))
        for event, extra in events:
            job = self._job(control, event, extra)
            if job is not None:
                self._submit(hub, control, job)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """threaded일 때 작업 중에 새로 생긴 작업까지 모두 끝날 때까지 기다린다 (테스트·종료용)."""
        with self._idle:
            return self._idle.wait_for(lambda: self._pending == 0, timeout)

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True)

    # --- 작업 ---------------------------------------------------------------------
    def _speech(self, snapshot, payload: Mapping) -> FinalUtterance:
        source = payload.get("source")
        return FinalUtterance(
            utterance_id=payload["utterance_id"], utterance_seq=payload["utterance_seq"],
            speaker_role=payload["speaker_role"], text=str(payload.get("text", "")),
            call_id=payload.get("call_id", snapshot.call_id),
            source=source if source in ("LIVE", "FIXTURE") else self._source.value,
        )

    def _job(self, control, event: str, extra: dict) -> _Job | None:
        try:
            snapshot, agent = _agent_snapshot(control)
        except (KeyError, TypeError, ValueError):
            return None  # C가 읽을 수 없는 상태(예: 등록되지 않은 목표)는 판단하지 않는다
        execution = ExecutionContext(trace_id=control.trace_id or new_id("trace"), source=self._source.value)
        request = B05Input(event=event, snapshot=agent, execution=execution, **extra)
        return _Job(event, request, snapshot.basis)

    def _submit(self, hub, control, job: _Job) -> None:
        if self._executor is not None:
            with self._idle:
                self._pending += 1
            self._executor.submit(self._run_counted, hub, control, job)
            return
        with self._lock:
            self._queue.append((hub, control, job))
            if self._draining:
                return
            self._draining = True
        try:
            while True:
                with self._lock:
                    if not self._queue:
                        self._draining = False
                        return
                    item = self._queue.popleft()
                self._run(*item)
        except BaseException:
            with self._lock:
                self._draining = False
            raise

    def _run_counted(self, hub, control, job: _Job) -> None:
        try:
            self._run(hub, control, job)
        finally:
            with self._idle:
                self._pending -= 1
                self._idle.notify_all()

    def _reader(self, control, help_id: str) -> Callable[[], AgentSnapshot | None]:
        def read() -> AgentSnapshot | None:
            try:
                snapshot, agent = _agent_snapshot(control)
            except (KeyError, TypeError, ValueError):
                return None
            return agent if snapshot.help_id == help_id else None

        return read

    def _run(self, hub, control, job: _Job) -> None:
        applied: list[tuple[str, str | None]] = []
        published = False

        def on_instruction(draft) -> None:
            nonlocal published
            published = True
            applied.append(self._apply(hub, control, "instruction.drafted", self._draft_payload(draft), job.basis)[1])

        try:
            result = self._workflow.run(job.request, read_current=self._reader(control, job.basis.help_id),
                                        on_instruction=on_instruction if job.event == "speech.final" else None)
        except Exception as error:  # C 쪽 예외가 세션 서버를 멈추지 않게 기록만 한다
            self.results.add(AgentRun(job.basis.help_id, job.event, None, None, type(error).__name__,
                                      tuple(applied)))
            return
        out = result.data
        if out is not None:
            if out.instruction_draft is not None and not published:
                # 최종 지시 초안은 on_instruction으로 이미 넣은 것과 같은 기록이다. 넣지 않았을 때만 넣는다.
                applied.append(self._apply(hub, control, "instruction.drafted",
                                           self._draft_payload(out.instruction_draft), job.basis)[1])
            if out.goal_draft is not None:
                g = out.goal_draft
                applied.append(self._apply(hub, control, "goal.drafted", {
                    "goal_id": g.goal_id, "approved_slots": dict(g.approved_slots),
                    "source_utterance_id": g.source_utterance_id}, job.basis)[1])
            if out.verdict is not None:
                applied.append(self._verdict(hub, control, out.verdict, job))
            if out.target is not None:
                applied.append(self._target(hub, control, out.target, job))
            if out.recovery is not None:
                applied.append(self._recovery(hub, control, out.recovery, job))
            if out.guide is not None:
                applied.append(self._guide(hub, control, out.guide, job))
            if out.arrival is not None:
                applied.append(self._arrival(hub, control, out.arrival, job))
        error = result.diagnostics.terminal_error
        self.results.add(AgentRun(job.basis.help_id, job.event, result.status, result.outcome,
                                  error.code if error else None, tuple(applied)))

    # --- 결과 → D -----------------------------------------------------------------
    @staticmethod
    def _draft_payload(draft) -> dict:
        return {"source_utterance_id": draft.source_utterance_id, "action": draft.action,
                "target_key": draft.target_key, "supersedes_previous": draft.supersedes_previous}

    @staticmethod
    def _apply(hub, control, kind: str, payload: dict, basis: Basis):
        if control.snapshot.help_id != basis.help_id:
            return None, (kind, "STALE_BASIS")
        update = hub.apply_internal(control, kind, payload, basis)
        state = update.state
        code = state.diagnostics.terminal_error.code if state.status == "error" else state.outcome
        return update, (kind, code)

    def _deliver(self, hub, control, kind: str, payload: dict) -> str:
        s = control.snapshot
        event = wire.control_event(kind, s.pair_id, s.call_id, s.help_id, payload)
        return "delivered" if hub.deliver(s.pair_id, Role(s.assisted_role), event) else "not_connected"

    def _target(self, hub, control, target, job: _Job) -> tuple[str, str | None]:
        update, record = self._apply(hub, control, TARGET_LOCATED, {
            "location_kind": target.location_kind, "target_id": target.target_id,
            "anchor_screen_revision": target.anchor_screen_revision,
            "evidence_codes": list(target.evidence_codes)}, job.basis)
        if update is None or update.state.status == "error":
            return record
        self._deliver(hub, control, TARGET_LOCATED, {
            "location_kind": target.location_kind, "target_id": target.target_id,
            "anchor_screen_revision": target.anchor_screen_revision,
            "evidence_codes": list(target.evidence_codes), "model_revision": target.model_revision,
            "basis": to_jsonable(job.basis)})
        return record

    def _verdict(self, hub, control, verdict, job: _Job) -> tuple[str, str | None]:
        body = {"verdict": verdict.verdict, "instruction_revision": verdict.instruction_revision,
                "from_screen_revision": verdict.from_screen_revision,
                "to_screen_revision": verdict.to_screen_revision, "evidence_codes": list(verdict.evidence_codes)}
        update, record = self._apply(hub, control, ACTION_ASSESSED, body, job.basis)
        if update is None or update.state.status == "error":
            return record
        self._deliver(hub, control, ACTION_ASSESSED,
                      {**body, "model_revision": verdict.model_revision, "basis": to_jsonable(job.basis)})
        return record

    def _recovery(self, hub, control, hint, job: _Job) -> tuple[str, str | None]:
        current = control.snapshot.basis
        if not _same(current, job.basis, ("help_id", "mode_revision", "instruction_revision",
                                          "projection_epoch", "screen_revision")):
            return RECOVERY_READY, "STALE_BASIS"
        return RECOVERY_READY, self._deliver(hub, control, RECOVERY_READY, {
            "instruction_revision": hint.instruction_revision,
            "anchor_screen_revision": hint.anchor_screen_revision, "first_edge_ref": hint.first_edge_ref,
            "path_distance": hint.path_distance, "basis": to_jsonable(current)})

    def _guide(self, hub, control, guide, job: _Job) -> tuple[str, str | None]:
        current = control.snapshot.basis
        if not _same(current, job.basis, ("help_id", "mode_revision", "goal_revision",
                                          "projection_epoch", "screen_revision")):
            return GUIDE_PROPOSED, "STALE_BASIS"
        return GUIDE_PROPOSED, self._deliver(hub, control, GUIDE_PROPOSED, {
            "decision_id": guide.decision_id, "mode": guide.mode, "action": guide.action,
            "target_id": guide.target_id, "message_key": guide.message_key, "confidence": guide.confidence,
            "valid_for_ms": guide.valid_for_ms, "basis": to_jsonable(job.basis)})

    def _arrival(self, hub, control, arrival, job: _Job) -> tuple[str, str | None]:
        """도착 추정은 N-22를 거친다 (결정 메모 5 (가)). 알릴 변화가 있으면 hub가 자녀에게 arrival.*를 보낸다."""
        try:
            result = hub.submit_arrival(control.snapshot.call_id, {
                "arrival_id": arrival.arrival_id, "arrived": arrival.arrived,
                "goal_revision": arrival.goal_revision, "evidence_codes": list(arrival.evidence_codes),
                "basis": _basis_dict(job.basis)})
        except wire.WireError as error:
            return "arrival", error.node_code or error.api_code
        code = result.diagnostics.terminal_error.code if result.status == "error" else result.outcome
        return "arrival", code
