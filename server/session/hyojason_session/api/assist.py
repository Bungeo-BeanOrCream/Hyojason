"""B-05 판단 연결: 세션 서버(D)가 C의 B05Workflow를 부르고 결과를 도움 상태와 앱에 반영한다.

D → C → D → 지원 대상 기기. 통합 진입점(server/mvp.py)은 앱 조립과 B 음성 서비스 연결만 맡고,
판단 호출과 결과 반영은 이 모듈이 맡는다 (#23·#27 연결 코드 일원화).

| D에서 일어난 일 | B-05 이벤트 | 결과 처리 |
|---|---|---|
| speech.final 적용 (N-11 turn.interpret / goal.interpret 효과) | speech.final | 지시 초안 → instruction.drafted, 목표 확률 → goal.belief, 목표 초안 → goal.drafted |
| instruction.drafted 적용 | instruction.accepted | TargetDecision → target.located |
| 지원 대상의 screen.observed | 통화 중 연속 전이면 observation.transition, 아니면 screen.observed | target.located·action.assessed, recovery·guide, 도착 추정 → N-22 |

- 앱으로는 지금 Android가 받는 `assist.decision` 하나로 묶어 지원 대상 기기에만 보낸다.
  판단마다 `decision_id`를 붙이고, 지원 대상이 `guide.shown`으로 같은 판단·같은 범위의 표시를 알려 와야
  도와주는 쪽 `help.view`의 `goal_context.guide_shown`을 켠다 (#27 보호자 상태, PR #29).
- 오래된 결과는 두 번 거른다: B-05의 read_current와 N-11의 STALE_BASIS.
- 발화 원문은 C에만 넘기고 로그·결과에 남기지 않는다.
- C 코드(server/agent)는 import만 한다. 저장소 루트가 sys.path에 있어야 한다 (pyproject pythonpath).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import logging
from secrets import token_urlsafe

from server.agent._execution import ExecutionContext
from server.agent.b05_workflow import AgentSnapshot, B05Input, B05Workflow, is_current
from server.agent.profiles import GOAL_LABELS
from server.agent.session_adapter import from_session
from server.contracts import FinalUtterance, ScreenState

from ..common.enums import Role, Source
from ..common.result import Basis as SessionBasis
from ..common.serialize import to_jsonable
from . import wire
from .hub import SessionHub

log = logging.getLogger("hyojason.session.assist")
_INTERPRET = frozenset({"turn.interpret", "goal.interpret"})


class AssistedSessionHub(SessionHub):
    """SessionHub에 B-05 판단 연결을 더한 허브. 판단은 이벤트 루프 밖 작업 스레드에서 돈다."""

    def __init__(self, *, workflow: B05Workflow, **kwargs) -> None:
        super().__init__(**kwargs)
        self.workflow = workflow
        self.tasks: set[asyncio.Task] = set()
        # call_id → (decision_id, 판단 당시 범위, 지원 대상 기기의 표시 확인 여부)
        self._guide_offers: dict[str, tuple[str, tuple, bool]] = {}

    # --- 확장 지점 -------------------------------------------------------------
    def analysis_allowed(self, pair_id: str) -> bool:
        """두 역할의 분석 동의가 유효한지. 음성 서비스가 동의를 가진 통합 서버가 재정의한다."""
        return True

    @staticmethod
    def _guide_scope(view) -> tuple:
        return (view.help_id, view.consent_revision, view.mode_revision, view.goal_revision,
                view.instruction_revision, view.projection_epoch, view.screen_revision, view.observation_health)

    # --- 보호자 상태 (help.view goal_context) ------------------------------------------
    def deliver(self, pair_id, role, event) -> bool:
        if event.type == wire.VIEW_EVENT:
            control = self.current_control(pair_id)
            if control is None:
                return super().deliver(pair_id, role, event)
            view = control.snapshot
            if view.help_state == "ENDED":
                self._guide_offers.pop(view.call_id, None)
            elif event.help_id == view.help_id:
                offer = self._guide_offers.get(view.call_id)
                shown = bool(offer and offer[2] and offer[1] == self._guide_scope(view))
                goal_id = view.goal.goal_id if view.goal and view.goal.goal_id in GOAL_LABELS else None
                if goal_id or shown:
                    context = {"goal_id": goal_id, "guide_shown": shown, "mode": view.mode}
                    context["arrival_suspected"] = control.arrival_state(view.help_id) == "SUSPECTED"
                    event = replace(event, payload={**event.payload, "goal_context": context})
        return super().deliver(pair_id, role, event)

    def handle_client(self, actor, message: object) -> dict:
        kind = message.get("type") if isinstance(message, dict) else None
        control = self.current_control(actor.pair_id) if kind == "guide.shown" else None
        if control is not None:
            # 표시 확인은 지금 범위에서 낸 마지막 판단(decision_id)에만 받는다.
            view = control.snapshot
            offer = self._guide_offers.get(view.call_id)
            payload = message.get("payload")
            if (not offer or not isinstance(payload, dict) or payload.get("decision_id") != offer[0]
                    or offer[1] != self._guide_scope(view)):
                return wire.rejected(message.get("event_id"), "CONFLICT", "STALE_BASIS")
        reply = super().handle_client(actor, message)
        if reply["type"] == wire.ACK and kind == "guide.shown":
            control = self.current_control(actor.pair_id)
            if control is not None:
                call_id = control.snapshot.call_id
                decision_id, scope, shown = self._guide_offers[call_id]
                if not shown:
                    self._guide_offers[call_id] = (decision_id, scope, True)
                    self._push_views(control)
        return reply

    # --- 입구 ---------------------------------------------------------------------
    def _apply(self, control, kind, role, payload=None, **kwargs):
        before = control.snapshot
        update = super()._apply(control, kind, role, payload, **kwargs)
        if update.state.status == "success" and update.state.outcome != "duplicate_ignored":
            after = control.snapshot
            interpret = {effect.kind for effect in update.state.data.side_effects} & _INTERPRET
            if kind == "speech.final" and interpret:
                # turn.interpret: 지시하는 쪽 발화(지시+목표), goal.interpret: 지원 대상 발화(목표만)
                self._schedule(control, kind, payload, instructs="turn.interpret" in interpret)
            elif kind == "screen.observed" and after.screen != before.screen and after.screen is not None:
                transition = (before.screen is not None and after.mode == "CALL_CHILD_LED"
                              and after.screen_revision != before.screen_revision)
                self._schedule(control, "observation.transition" if transition else kind,
                               before=before.screen if transition else None)
            elif kind == "instruction.drafted" and after.screen is not None:
                self._schedule(control, "instruction.accepted")
        return update

    def agent_snapshot(self, control) -> AgentSnapshot:
        """같은 잠금 안에서 읽은 N-11 스냅샷과 방문 이력을 C의 입력으로 바꾼다."""
        with control._lock:
            view = control.snapshot
            return from_session(view, control.trail, basis=view.basis,
                                processing_allowed=(view.help_state != "ENDED"
                                                    and self.analysis_allowed(view.pair_id)))

    # --- 판단 실행 -----------------------------------------------------------------
    def _schedule(self, control, event, turn=None, *, before=None, instructs=True) -> None:
        view = self.agent_snapshot(control)
        if not view.processing_allowed:
            return
        speech = None
        recent: tuple[FinalUtterance, ...] = ()
        source = self._source.value
        if turn is not None:
            source = "LIVE" if source == "LIVE" and turn.get("source") == "stt" else "FIXTURE"
            # C의 N-15는 지시하는 쪽을 CHILD, 지원 대상을 PARENT로 본다. D가 화자 역할을 이미 확인했다.
            helper = "PARENT" if control.snapshot.assisted_role == "CHILD" else "CHILD"

            def relative(role: str) -> str:
                return "CHILD" if role == helper else "PARENT"

            fields = {key: turn[key] for key in ("utterance_id", "utterance_seq", "text", "call_id")}
            speech = FinalUtterance(**fields, source=source,
                                    speaker_role="CHILD" if instructs else "PARENT")
            recent = tuple(
                FinalUtterance(utterance_id=item["utterance_id"], utterance_seq=item["utterance_seq"],
                               speaker_role=relative(item["speaker_role"]), text=item["text"],
                               call_id=speech.call_id, source=source)
                for item in control.recent_utterances
            )
        request = B05Input(event=event, snapshot=view, speech=speech, recent_utterances=recent,
                           assisted_role=control.snapshot.assisted_role,
                           before=ScreenState(**asdict(before)) if before is not None else None,
                           execution=ExecutionContext(trace_id=control.trace_id, source=source))
        task = asyncio.create_task(self._evaluate(control, request))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _evaluate(self, control, request: B05Input) -> None:
        try:
            result = await asyncio.to_thread(self.workflow.run, request,
                                             read_current=lambda: self.agent_snapshot(control))
            log.info("agent %s %s: %s %s", result.diagnostics.source, request.event, result.status,
                     result.outcome or result.diagnostics.terminal_error.code)
            if result.data is not None:
                self._publish_result(control, request, result.data)
        except Exception as error:  # 통화를 살리고 발화 원문은 로그에 남기지 않는다.
            if self._source is Source.FIXTURE:
                log.exception("fixture agent integration failed")
            else:
                log.error("agent integration failed: %s", type(error).__name__)

    def _publish_result(self, control, request, data) -> None:
        if not is_current(request, self.agent_snapshot(control)):
            return
        basis = SessionBasis(**asdict(request.snapshot.basis))
        if data.instruction_draft:
            self._apply(control, "instruction.drafted", "SERVER", asdict(data.instruction_draft),
                        basis=basis)
        if data.goal_belief:
            # 목표 확률을 먼저 저장하고, 1위가 바뀐 경우에만 목표 초안을 넣는다.
            self._apply(control, "goal.belief", "SERVER", asdict(data.goal_belief), basis=basis)
        if data.goal_draft:
            self._apply(control, "goal.drafted", "SERVER", asdict(data.goal_draft), basis=basis)
        if data.target:
            update = self._apply(control, "target.located", "SERVER", asdict(data.target), basis=basis)
            if update.state.status != "success":
                log.warning("target result rejected: %s", update.state.diagnostics.terminal_error.code)
                return
        if data.verdict:
            update = self._apply(control, "action.assessed", "SERVER", asdict(data.verdict), basis=basis)
            if update.state.status != "success":
                return
        if data.arrival:
            self.submit_arrival(basis.call_id, to_jsonable(data.arrival))
        if data.target or data.verdict or data.recovery or data.guide or data.warning:
            current = control.snapshot
            decision_id = token_urlsafe(18)
            scope = self._guide_scope(current)
            previous = self._guide_offers.get(current.call_id)
            self._guide_offers[current.call_id] = (
                decision_id, scope, bool(previous and previous[1] == scope and previous[2]))
            payload = {"basis": to_jsonable(current.basis), "trace_id": control.trace_id, "decision_id": decision_id,
                       "target": to_jsonable(data.target), "verdict": to_jsonable(data.verdict),
                       "recovery": to_jsonable(data.recovery), "guide": to_jsonable(data.guide),
                       "warning": data.warning}
            self.deliver(current.pair_id, Role(current.assisted_role), wire.control_event(
                "assist.decision", current.pair_id, current.call_id, current.help_id, payload))
