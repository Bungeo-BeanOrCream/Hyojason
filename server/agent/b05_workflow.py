"""B-05의 권위 있는 상태 입력과 모드별 판단 진입점."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..contracts import (
    ArrivalDecision,
    Basis,
    Candidate,
    CandidateSet,
    FinalUtterance,
    Goal,
    GoalBelief,
    GoalDraft,
    GuideProposal,
    Instruction,
    InstructionDraft,
    Mode,
    NavigationTrail,
    NodeResult,
    RecoveryHint,
    ScreenState,
    TargetDecision,
)
from ._execution import ExecutionContext, NodeRun
from .assess_instruction_follow_through import (
    ActionVerdict,
    AssessInstructionFollowThrough,
    AssessInstructionFollowThroughInput,
)
from .assess_post_call_arrival import AssessPostCallArrival, AssessPostCallArrivalInput
from .choose_post_call_action import ChoosePostCallAction, ChoosePostCallActionInput
from .enumerate_action_candidate import (
    EnumerateActionCandidates,
    EnumerateActionCandidatesInput,
)
from .infer_zoro_choice import InferZoroChoice, ZoroChoiceRunner
from .interpret_child_turn import InterpretChildTurn, InterpretChildTurnInput
from .locate_child_instruction_target import (
    LocateChildInstructionTarget,
    LocateChildInstructionTargetInput,
)
from .profiles import (
    POLICY_REVISION,
    arrival_evidence,
    is_protected,
    is_stable,
    target_profile,
)

AgentEvent = Literal[
    "speech.final",
    "instruction.accepted",
    "screen.observed",
    "observation.transition",
    "call.ended",
    "goal.updated",
]
RISK_WARNING = "되돌리기 어려운 동작이에요. 천천히 확인하세요"


@dataclass(frozen=True, kw_only=True)
class AgentSnapshot:
    """D가 인증·동의·도움 상태를 적용한 뒤 B-05에 넘기는 스냅샷."""

    mode: Mode
    basis: Basis
    processing_allowed: bool
    screen: ScreenState | None
    instruction: Instruction | None
    goal: Goal | None
    trail: NavigationTrail
    goal_belief: GoalBelief | None = None


@dataclass(frozen=True, kw_only=True)
class B05Input:
    event: AgentEvent
    snapshot: AgentSnapshot
    speech: FinalUtterance | None = None
    # 현재 도움의 최근 확정 발화 창(두 역할). 목표 확률 판단에만 쓰고 저장하지 않는다.
    recent_utterances: tuple[FinalUtterance, ...] = ()
    # 이번 도움의 지원 대상 가족 역할. 모델 입력의 화자 이름을 실제 역할로 붙일 때 쓴다.
    assisted_role: Literal["PARENT", "CHILD"] = "PARENT"
    before: ScreenState | None = None
    target: TargetDecision | None = None
    policy_revision: int = POLICY_REVISION
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class B05Output:
    instruction_draft: InstructionDraft | None = None
    goal_belief: GoalBelief | None = None
    goal_draft: GoalDraft | None = None
    target: TargetDecision | None = None
    verdict: ActionVerdict | None = None
    recovery: RecoveryHint | None = None
    guide: GuideProposal | None = None
    arrival: ArrivalDecision | None = None
    warning: str | None = None
    supersedes_previous: bool = False


SnapshotReader = Callable[[], AgentSnapshot | None]


class B05Workflow:
    node_id = "B-05"

    def __init__(self, *, infer_zoro_choice: ZoroChoiceRunner | None = None) -> None:
        infer = infer_zoro_choice or InferZoroChoice.from_environment().run
        self._interpret = InterpretChildTurn(infer_zoro_choice=infer)
        self._search = EnumerateActionCandidates()
        self._locate = LocateChildInstructionTarget(infer_zoro_choice=infer)
        self._assess = AssessInstructionFollowThrough(infer_zoro_choice=infer)
        self._choose = ChoosePostCallAction(infer_zoro_choice=infer)
        self._arrival = AssessPostCallArrival(infer_zoro_choice=infer)

    def run(
        self,
        request: B05Input,
        *,
        read_current: SnapshotReader,
        on_instruction: Callable[[InstructionDraft], None] | None = None,
    ) -> NodeResult[B05Output]:
        snapshot = request.snapshot
        run = NodeRun(self.node_id, snapshot.basis, request.execution)
        if not snapshot.processing_allowed:
            return run.fail("NO_CONSENT", "validate")
        problem = snapshot_problem(snapshot)
        if problem is not None:
            return run.fail(problem, "validate")
        if not is_current(request, read_current()):
            return run.fail("STALE_BASIS", "validate")
        run.ok("validate")
        if request.event == "speech.final":
            result = self._on_speech(request, run, read_current, on_instruction)
        elif request.event == "call.ended" and snapshot.mode != "POST_CALL_AGENT":
            # RTC 종료는 먼저 N-11에 적용된다.
            result = self._wait(run, "mode", "HANDOFF_PENDING")
        elif snapshot.screen is not None and is_protected(snapshot.screen):
            result = self._wait(run, "screen", "PROTECTED_SCREEN")
        elif snapshot.screen is None or not is_stable(snapshot.screen):
            result = self._wait(run, "screen", "NO_STABLE_SCREEN")
        elif snapshot.mode == "CALL_CHILD_LED":
            result = self._live(request, snapshot.screen, run)
        else:
            result = self._post_call(request, snapshot.screen, run)

        if not is_current(request, read_current()):
            return run.fail("STALE_BASIS", "publish")
        return result

    def _on_speech(
        self,
        request: B05Input,
        run: NodeRun,
        read_current: SnapshotReader,
        on_instruction: Callable[[InstructionDraft], None] | None,
    ) -> NodeResult[B05Output]:
        snapshot, speech = request.snapshot, request.speech
        if speech is None:
            return run.fail("INVALID_INPUT", "speech")
        # 목표 확률은 두 역할의 발화로 갱신한다. 지시 해석은 N-15가 자녀 발화에만 한다.
        if snapshot.mode != "CALL_CHILD_LED":
            return self._wait(run, "N-15", "NOT_APPLICABLE")

        def publish_draft(draft: InstructionDraft) -> None:
            if on_instruction is not None and is_current(request, read_current()):
                on_instruction(draft)

        result = self._interpret.run(
            InterpretChildTurnInput(
                mode=snapshot.mode,
                speech=speech,
                existing_goal=snapshot.goal,
                goal_belief=snapshot.goal_belief,
                recent_utterances=request.recent_utterances,
                assisted_role=request.assisted_role,
                goal_catalog_revision=request.policy_revision,
                basis=snapshot.basis,
                execution=run.for_child(),
            ),
            on_instruction=publish_draft,
        )
        run.child(result)
        if result.data is None:
            return run.propagate(result, "N-15")
        value = result.data
        output = B05Output(
            instruction_draft=value.instruction_draft,
            goal_belief=value.goal_belief,
            goal_draft=value.goal_draft,
            warning=RISK_WARNING if result.outcome == "policy_denied" else None,
            supersedes_previous=value.supersedes_previous,
        )
        if result.status == "partial":
            return run.finish("search_incomplete", output, status="partial")
        if value.goal_draft:
            return run.finish("goal_captured", output)
        return run.finish("waiting", output)

    def _live(
        self, request: B05Input, screen: ScreenState, run: NodeRun
    ) -> NodeResult[B05Output]:
        snapshot = request.snapshot
        instruction = snapshot.instruction
        if instruction is None:
            return self._wait(run, "N-16", "NO_ACTIVE_INSTRUCTION")
        if request.event == "observation.transition":
            before = request.before
            if before is None:
                return run.fail("INVALID_INPUT", "observation")
            target = instruction.target_key
            returned = bool(
                target
                and target_profile(target, screen.screen_kind)
                and not target_profile(target, before.screen_kind)
            )
            layout_only = (
                before.projection_epoch == screen.projection_epoch
                and before.screen_revision == screen.screen_revision
            )
            if not returned and not layout_only:
                return self._observe_action(request, screen, instruction, run)
        elif request.event not in {"instruction.accepted", "screen.observed"}:
            return self._wait(run, "N-16", "NOT_APPLICABLE")

        found = self._candidates(request, screen, run)
        if found.data is None:
            return run.propagate(found, "N-16")
        if not found.data.candidates:
            run.skip("N-27", "NO_CANDIDATES")
            return self._from_search(run, found, "waiting", B05Output())
        location = self._locate.run(
            LocateChildInstructionTargetInput(
                mode=snapshot.mode,
                instruction=instruction,
                candidate_set=found.data,
                screen=screen,
                basis=snapshot.basis,
                execution=run.for_child(),
            )
        )
        run.child(location)
        if location.data is None:
            return run.propagate(location, "N-27")
        outcome = "live_evidence_ready" if location.data.target_id else "waiting"
        return self._from_search(run, found, outcome, B05Output(target=location.data))

    def _observe_action(
        self,
        request: B05Input,
        screen: ScreenState,
        instruction: Instruction,
        run: NodeRun,
    ) -> NodeResult[B05Output]:
        snapshot, before = request.snapshot, request.before
        if before is None:
            return run.fail("INVALID_INPUT", "observation")
        if snapshot.trail.gap_after_seq is not None and not _transition_is_in_trail(
            snapshot.trail, before, screen
        ):
            return self._wait(run, "N-28", "CAPTURE_GAP")
        assessment = self._assess.run(
            AssessInstructionFollowThroughInput(
                mode=snapshot.mode,
                instruction=instruction,
                before=before,
                after=screen,
                target=request.target,
                basis=snapshot.basis,
                execution=run.for_child(),
            )
        )
        run.child(assessment)
        verdict = assessment.data
        if verdict is None:
            return run.propagate(assessment, "N-28")
        if verdict.verdict != "WRONG":
            return run.finish("live_evidence_ready", B05Output(verdict=verdict))

        found = self._candidates(request, screen, run, recovery_only=True)
        if found.data is None:
            error = found.diagnostics.terminal_error
            if error and error.code == "SEARCH_INCOMPLETE":
                return run.finish(
                    "search_incomplete", B05Output(verdict=verdict), status="partial"
                )
            return run.propagate(found, "N-16")
        recovery = _recovery_hint(found.data.candidates, instruction, snapshot.basis)
        return self._from_search(
            run,
            found,
            "live_evidence_ready",
            B05Output(verdict=verdict, recovery=recovery),
        )

    def _post_call(
        self, request: B05Input, screen: ScreenState, run: NodeRun
    ) -> NodeResult[B05Output]:
        snapshot = request.snapshot
        goal = snapshot.goal
        if goal is None:
            return self._wait(run, "goal", "NO_ACTIVE_GOAL")
        evidence = arrival_evidence(goal, screen)
        if evidence:
            arrival = self._arrival.run(
                AssessPostCallArrivalInput(
                    mode=snapshot.mode,
                    goal=goal,
                    screen=screen,
                    evidence_codes=evidence,
                    basis=snapshot.basis,
                    execution=run.for_child(),
                )
            )
            run.child(arrival)
            if arrival.data is None:
                return run.propagate(arrival, "N-18")
            if arrival.data.arrived and arrival.data.arrival_id:
                run.skip("N-17", "ARRIVAL_CONFIRMED")
                return run.finish(
                    "post_arrival",
                    B05Output(
                        arrival=ArrivalDecision(
                            arrival_id=arrival.data.arrival_id,
                            arrived=True,
                            goal_revision=goal.goal_revision,
                            evidence_codes=arrival.data.evidence_codes,
                            basis=snapshot.basis,
                        )
                    ),
                )
        else:
            run.skip("N-18", "INSUFFICIENT_EVIDENCE")

        found = self._candidates(request, screen, run)
        if found.data is None:
            return run.propagate(found, "N-16")
        if not found.data.candidates:
            run.skip("N-17", "NO_CANDIDATES")
            return self._from_search(run, found, "waiting", B05Output())
        best = self._choose.run(
            ChoosePostCallActionInput(
                mode=snapshot.mode,
                candidate_set=found.data,
                goal=goal,
                basis=snapshot.basis,
                threshold_profile="p0",
                execution=run.for_child(),
            )
        )
        run.child(best)
        if best.data is None:
            return run.propagate(best, "N-17")
        return self._from_search(
            run,
            found,
            "post_action_ready" if best.data.proposal else "waiting",
            B05Output(guide=best.data.proposal),
        )

    def _candidates(
        self,
        request: B05Input,
        screen: ScreenState,
        run: NodeRun,
        *,
        recovery_only: bool = False,
    ) -> NodeResult[CandidateSet]:
        snapshot = request.snapshot
        result = self._search.run(
            EnumerateActionCandidatesInput(
                mode=snapshot.mode,
                screen=screen,
                instruction=snapshot.instruction,
                goal=snapshot.goal,
                trail=snapshot.trail,
                policy_revision=request.policy_revision,
                basis=snapshot.basis,
                execution=run.for_child(),
                recovery_only=recovery_only,
            )
        )
        run.child(result)
        return result

    @staticmethod
    def _from_search(
        run: NodeRun, search: NodeResult[CandidateSet], outcome: str, output: B05Output
    ) -> NodeResult[B05Output]:
        if search.status == "partial":
            return run.finish("search_incomplete", output, status="partial")
        return run.finish(outcome, output)

    @staticmethod
    def _wait(run: NodeRun, step: str, code: str) -> NodeResult[B05Output]:
        run.skip(step, code)
        return run.finish("waiting", B05Output())


def snapshot_problem(snapshot: AgentSnapshot) -> str | None:
    basis = snapshot.basis
    if (
        snapshot.mode not in {"CALL_CHILD_LED", "POST_CALL_AGENT"}
        or not all((basis.pair_id, basis.call_id, basis.help_id))
        or basis.consent_revision is None
        or basis.mode_revision is None
    ):
        return "INVALID_INPUT"
    if snapshot.screen and (
        snapshot.screen.projection_epoch != basis.projection_epoch
        or snapshot.screen.screen_revision != basis.screen_revision
    ):
        return "STALE_BASIS"
    if (
        snapshot.instruction
        and snapshot.instruction.instruction_revision != basis.instruction_revision
    ):
        return "STALE_BASIS"
    if snapshot.goal and snapshot.goal.goal_revision != basis.goal_revision:
        return "STALE_BASIS"
    return None


def is_current(request: B05Input, current: AgentSnapshot | None) -> bool:
    expected = request.snapshot
    if (
        current is None
        or not current.processing_allowed
        or current.mode != expected.mode
    ):
        return False
    old, new = expected.basis, current.basis
    scope = "pair_id", "call_id", "help_id", "consent_revision", "mode_revision"
    if any(getattr(old, field) != getattr(new, field) for field in scope):
        return False
    if request.event == "speech.final":
        return old.utterance_seq == new.utterance_seq
    if expected.screen != current.screen:
        return False
    if current.mode == "CALL_CHILD_LED":
        same_instruction = (
            old.instruction_revision == new.instruction_revision
            and expected.instruction == current.instruction
        )
        if request.event == "observation.transition":
            return same_instruction and expected.trail == current.trail
        return same_instruction
    return (
        old.goal_revision == new.goal_revision
        and expected.goal == current.goal
        and expected.trail == current.trail
    )


def _transition_is_in_trail(
    trail: NavigationTrail, before: ScreenState, after: ScreenState
) -> bool:
    gap = trail.gap_after_seq
    return any(
        (gap is None or edge.observed_seq > gap)
        and edge.projection_epoch == before.projection_epoch == after.projection_epoch
        and edge.from_screen.screen_revision == before.screen_revision
        and edge.to_screen.screen_revision == after.screen_revision
        for edge in trail.entries
    )


def _recovery_hint(
    candidates: tuple[Candidate, ...], instruction: Instruction, basis: Basis
) -> RecoveryHint | None:
    anchor = instruction.origin_screen_revision
    if anchor is None:
        return None
    for candidate in candidates:
        if candidate.action == "BACK" and candidate.path_distance is not None:
            return RecoveryHint(
                instruction_revision=instruction.instruction_revision,
                anchor_screen_revision=anchor,
                first_edge_ref=candidate.choice_id,
                path_distance=candidate.path_distance,
                basis=basis,
            )
    return None
