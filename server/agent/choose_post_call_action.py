"""N-17. 통화 종료 후 목표로 이어지는 다음 행동 하나를 고른다.

입력은 검증된 후보 집합, 목표, Basis, 확신도 기준이다.
출력은 안내 제안 또는 보류 사유다.
통화 후 모드에서만 N-14로 선택하고, 후보나 근거가 부족하면 보류한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid5, NAMESPACE_URL

from ..contracts import Basis, CandidateSet, Goal, GuideProposal, Mode, NodeResult
from ._execution import ExecutionContext, NodeRun
from .infer_zoro_choice import (
    Choice,
    InferZoroChoiceInput,
    ZoroChoiceRunner,
    approved_selection,
)
from .profiles import GOAL_LABELS, TARGETS

# 통화 후 안내는 화면이 안정된 뒤 3초(GuideGate.DELAY_MS)를 기다려 표시한다. 4500ms이면 실제로 1.5초만
# 보이고, 같은 Basis는 다시 추론하지 않아(contracts.md 제안 만료) 부모가 읽기 전에 사라졌다(#37 실측).
# 설정 화면 알림의 15초 만료와 맞춘다. 화면이 바뀌면 Basis가 달라져 그 전에 폐기된다.
PROPOSAL_VALID_MS = 15_000


@dataclass(frozen=True, kw_only=True)
class ChoosePostCallActionInput:
    mode: Mode
    candidate_set: CandidateSet
    goal: Goal
    basis: Basis
    threshold_profile: str
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class ChoosePostCallActionOutput:
    proposal: GuideProposal | None
    reason: str
    model_revision: str | None
    selected_id: str | None


@dataclass(kw_only=True)
class ChoosePostCallAction:
    infer_zoro_choice: ZoroChoiceRunner
    node_id = "N-17"

    def run(
        self, request: ChoosePostCallActionInput
    ) -> NodeResult[ChoosePostCallActionOutput]:
        basis = request.basis
        run = NodeRun(self.node_id, basis, request.execution)
        if request.mode != "POST_CALL_AGENT" or request.threshold_profile != "p0":
            return run.fail("INVALID_INPUT", "validate")
        if (
            request.goal.goal_revision != basis.goal_revision
            or basis.screen_revision is None
        ):
            return run.fail("STALE_BASIS", "validate")
        if request.goal.goal_id not in GOAL_LABELS:
            return run.fail("INVALID_INPUT", "validate")
        run.ok("validate")

        eligible = [
            candidate
            for candidate in request.candidate_set.candidates
            if candidate.path_distance is not None
            and (
                candidate.action == "BACK"
                and request.candidate_set.coverage == "complete"
                or candidate.target_id in TARGETS
                and candidate.action in {"TAP", "INPUT_HINT"}
                and (
                    request.candidate_set.coverage == "complete"
                    or request.candidate_set.coverage == "partial"
                    and candidate.action == "TAP"
                )
            )
        ]
        if not eligible:
            return run.finish(
                "waiting",
                ChoosePostCallActionOutput(
                    proposal=None,
                    reason="no_candidates",
                    model_revision=None,
                    selected_id=None,
                ),
            )
        closest = min(
            candidate.path_distance
            for candidate in eligible
            if candidate.path_distance is not None
        )
        options = {
            candidate.choice_id: candidate
            for candidate in eligible
            if candidate.path_distance == closest
        }
        choices = tuple(
            Choice(
                choice_id=key,
                description=TARGETS[candidate.target_id].label
                if candidate.target_id
                else "검증된 화면으로 뒤로 가기",
            )
            for key, candidate in options.items()
        )
        model = self.infer_zoro_choice(
            InferZoroChoiceInput(
                mode=request.mode,
                question_kind="NEXT_ACTION",
                choices=(
                    *choices,
                    Choice(choice_id="WAIT", description="현재 단계 안내 보류"),
                ),
                context={
                    "goal": GOAL_LABELS[request.goal.goal_id],
                    "approved_slots": request.goal.approved_slots,
                },
                basis=basis,
                deadline_ms=1000,
                execution=run.for_child(),
            )
        )
        run.child(model)
        if model.data is None:
            return run.propagate(model, "N-14")
        winner = approved_selection(model.data, set(options))
        if winner is None:
            return run.finish(
                "waiting",
                ChoosePostCallActionOutput(
                    proposal=None,
                    reason="wait"
                    if model.data.selected_id == "WAIT"
                    else "low_confidence",
                    model_revision=model.data.model_revision,
                    selected_id=model.data.selected_id,
                ),
            )
        selected = options[winner]
        decision_key = (
            f"{basis.help_id}:{basis.mode_revision}:{basis.goal_revision}:"
            f"{basis.projection_epoch}:{basis.screen_revision}:{winner}"
        )
        proposal = GuideProposal(
            decision_id=uuid5(NAMESPACE_URL, decision_key).hex,
            mode=request.mode,
            action=selected.action,
            target_id=selected.target_id,
            message_key=selected.label_key,
            confidence=model.data.probabilities[winner],
            valid_for_ms=PROPOSAL_VALID_MS,
            basis=basis,
        )
        return run.finish(
            "proposed",
            ChoosePostCallActionOutput(
                proposal=proposal,
                reason="selected",
                model_revision=model.data.model_revision,
                selected_id=winner,
            ),
        )
