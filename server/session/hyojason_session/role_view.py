"""N-21 · project_role_view — 부모·자녀 화면에 각각 허용된 상태만 투영한다.

docs/small-nodes/N-21.md
- 결과: success/view_ready, success/view_waiting, error/null
- 해당 역할의 view만 채우고 다른 역할 view는 null
- 자녀에게는 call_state, can_complete, arrival_indicator만 보낸다
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .common.enums import ArrivalState, CallState, EndReason, HelpState, Mode, Role, Source
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, ResultRef, StepStatus
from .common.types import ActorContext, HelpSnapshot, snapshot_problems
from .views import (
    COMPLETION_MESSAGE_KEY,
    ArrivalIndicator,
    ChildView,
    ParentGoalView,
    ParentInstructionView,
    ParentStatus,
    ParentView,
)

NODE_ID = "N-21"
OUTCOME_VIEW_READY = "view_ready"
OUTCOME_VIEW_WAITING = "view_waiting"

_OPEN_HELP_STATES = (HelpState.ACTIVE, HelpState.ARRIVAL_PENDING)


@dataclass(frozen=True)
class RoleViewInput:
    snapshot: HelpSnapshot
    viewer_role: Role
    arrival_state: ArrivalState  # N-22의 도착 추정 알림 상태
    online: bool  # 조회하는 역할의 제어 채널이 서버에 연결돼 있는가


@dataclass(frozen=True)
class RoleViewData:
    role: Role
    parent_view: ParentView | None
    child_view: ChildView | None


def project_role_view(
    inp: RoleViewInput,
    *,
    actor: ActorContext,
    latest_help_id: str | None,
    trace_id: str,
    source: Source = Source.LIVE,
    input_refs: Iterable[ResultRef] = (),
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> NodeResult[RoleViewData]:
    """역할별 view 하나를 만든다.

    actor: 인증 토큰에서 얻은 호출자. viewer_role·snapshot.pair_id와 대조한다.
    latest_help_id: 서버(N-11)가 이 페어의 최신 도움으로 가리키는 help_id.
        다르면 '이전 도움의 snapshot'으로 보고 거절한다.
    """

    run = NodeRun(NODE_ID, trace_id, source, clock_ns)
    refs = tuple(input_refs)
    snapshot = inp.snapshot

    # 1. 입력 검사
    bad = snapshot_problems(snapshot)
    if not isinstance(inp.viewer_role, Role):
        bad.append("viewer_role")
    if not isinstance(inp.arrival_state, ArrivalState):
        bad.append("arrival_state")
    if not isinstance(inp.online, bool):
        bad.append("online")
    if bad:
        basis = Basis(pair_id=actor.pair_id)
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", basis, input_refs=refs)
    run.step("validate_input", StepStatus.SUCCESS)
    basis = Basis.from_snapshot(snapshot)

    # 2. 권한과 스코프: 다른 페어 → UNAUTHORIZED, 이전 도움 → STALE_BASIS
    if actor.role is not inp.viewer_role or actor.pair_id != snapshot.pair_id:
        return run.error(ErrorCode.UNAUTHORIZED, "authorize_scope", basis, input_refs=refs)
    if latest_help_id is None or snapshot.help_id != latest_help_id:
        return run.error(ErrorCode.STALE_BASIS, "authorize_scope", basis, input_refs=refs)
    run.step("authorize_scope", StepStatus.SUCCESS)

    # 3. 역할 기준 필드 선택과 조합
    waiting = _is_waiting(snapshot)
    arrival_shown = _arrival_visible(snapshot, inp.arrival_state)
    if inp.arrival_state is ArrivalState.SUSPECTED and not arrival_shown:
        # 통화 중이거나 도움이 끝난 뒤 들어온 도착 추정은 어느 역할에도 보이지 않는다.
        run.step("select_fields", StepStatus.SUCCESS, "ARRIVAL_SUPPRESSED")
    else:
        run.step("select_fields", StepStatus.SUCCESS)

    if inp.viewer_role is Role.PARENT:
        data = RoleViewData(
            role=Role.PARENT,
            parent_view=_parent_view(snapshot, waiting, arrival_shown),
            child_view=None,
        )
    else:
        data = RoleViewData(
            role=Role.CHILD,
            parent_view=None,
            child_view=_child_view(snapshot, arrival_shown, inp.online),
        )
    run.step("compose_view", StepStatus.SUCCESS)

    outcome = OUTCOME_VIEW_WAITING if waiting else OUTCOME_VIEW_READY
    return run.success(outcome, data, basis, input_refs=refs)


def _is_waiting(s: HelpSnapshot) -> bool:
    """활성 도움인데 아직 지시도 목표도 없다 (21.2)."""

    if s.help_state is HelpState.ENDED:
        return False
    return s.help_state is HelpState.LISTENING or (s.goal is None and s.instruction is None)


def _arrival_visible(s: HelpSnapshot, arrival_state: ArrivalState) -> bool:
    """도착 추정은 통화 후, 도움이 열려 있을 때만 나타난다."""

    return (
        arrival_state is ArrivalState.SUSPECTED
        and s.mode is Mode.POST_CALL_AGENT
        and s.call_state is CallState.ENDED
        and s.help_state in _OPEN_HELP_STATES
    )


def _parent_status(s: HelpSnapshot, waiting: bool, arrival_shown: bool) -> ParentStatus:
    if s.help_state is HelpState.ENDED:
        if s.end_reason is EndReason.CHILD_COMPLETED:
            return ParentStatus.COMPLETED
        if s.end_reason is EndReason.PARENT_STOPPED:
            return ParentStatus.STOPPED
        return ParentStatus.ENDED
    if waiting:
        return ParentStatus.LISTENING
    if s.mode is Mode.POST_CALL_AGENT:
        return ParentStatus.ARRIVAL_PENDING if arrival_shown else ParentStatus.POST_CALL_GUIDING
    return ParentStatus.CALL_GUIDING


def _parent_view(s: HelpSnapshot, waiting: bool, arrival_shown: bool) -> ParentView:
    ended = s.help_state is HelpState.ENDED
    goal = None
    if s.goal is not None and not ended:
        goal = ParentGoalView(
            goal_id=s.goal.goal_id,
            goal_revision=s.goal.goal_revision,
            approved_slots=dict(s.goal.approved_slots),
        )
    instruction = None
    # 통화 후에는 통화 중 지시가 취소되므로 싣지 않는다.
    if s.instruction is not None and not ended and s.mode is Mode.CALL_CHILD_LED:
        instruction = ParentInstructionView(
            instruction_id=s.instruction.instruction_id,
            instruction_revision=s.instruction.instruction_revision,
            action=s.instruction.action,
            target_key=s.instruction.target_key,
        )
    return ParentView(
        status=_parent_status(s, waiting, arrival_shown),
        call_state=s.call_state,
        help_state=s.help_state,
        mode=s.mode,
        mode_revision=s.mode_revision,
        goal=goal,
        instruction=instruction,
        # 안내 끄기는 네트워크와 무관하게 로컬에서 바로 정리돼야 하므로 online과 묶지 않는다 (N-23).
        can_stop_guidance=not ended,
        completion_message_key=(
            COMPLETION_MESSAGE_KEY
            if ended and s.end_reason is EndReason.CHILD_COMPLETED
            else None
        ),
    )


def _child_view(s: HelpSnapshot, arrival_shown: bool, online: bool) -> ChildView:
    return ChildView(
        call_state=s.call_state,
        can_complete=online and s.help_state in _OPEN_HELP_STATES,
        arrival_indicator=ArrivalIndicator.SUSPECTED if arrival_shown else ArrivalIndicator.NONE,
    )
