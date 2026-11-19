"""N-18. 통화 종료 후 부모가 목표 화면에 도달했는지 추정한다.

입력은 목표, 최신 화면, 목표별 완료 단서, Basis다.
출력은 도착 추정 여부, 근거 코드, 확신도와 도착 ID다.
화면 근거가 충족될 때 N-14에 묻고, 도착 추정을 자녀의 최종 완료와 구분한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid5, NAMESPACE_URL

from ..contracts import Basis, Goal, Mode, NodeResult, ScreenState
from ._execution import ExecutionContext, NodeRun
from .infer_zoro_choice import (
    Choice,
    InferZoroChoiceInput,
    ZoroChoiceRunner,
    approved_selection,
)
from .profiles import GOAL_LABELS, arrival_evidence


@dataclass(frozen=True, kw_only=True)
class AssessPostCallArrivalInput:
    mode: Mode
    goal: Goal
    screen: ScreenState
    evidence_codes: tuple[str, ...]
    basis: Basis
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class AssessPostCallArrivalOutput:
    arrived: bool
    evidence_codes: tuple[str, ...]
    confidence: float | None
    arrival_id: str | None


@dataclass(kw_only=True)
class AssessPostCallArrival:
    infer_zoro_choice: ZoroChoiceRunner
    node_id = "N-18"

    def run(
        self, request: AssessPostCallArrivalInput
    ) -> NodeResult[AssessPostCallArrivalOutput]:
        basis = request.basis
        run = NodeRun(self.node_id, basis, request.execution)
        if request.mode != "POST_CALL_AGENT":
            return run.fail("NOT_APPLICABLE", "validate")
        if (
            request.goal.goal_revision != basis.goal_revision
            or request.screen.projection_epoch != basis.projection_epoch
            or request.screen.screen_revision != basis.screen_revision
        ):
            return run.fail("STALE_BASIS", "validate")
        run.ok("validate")
        evidence = arrival_evidence(request.goal, request.screen)
        if not evidence or not set(evidence).issubset(request.evidence_codes):
            run.skip("model", "INSUFFICIENT_EVIDENCE")
            return run.finish(
                "not_arrived",
                AssessPostCallArrivalOutput(
                    arrived=False, evidence_codes=(), confidence=None, arrival_id=None
                ),
            )
        model = self.infer_zoro_choice(
            InferZoroChoiceInput(
                mode=request.mode,
                question_kind="ARRIVAL",
                choices=(
                    Choice(
                        choice_id="ARRIVED",
                        description="화면에서 목표의 모든 완료 근거가 확인됨",
                    ),
                    Choice(choice_id="NOT_ARRIVED", description="완료가 입증되지 않음"),
                ),
                context={
                    "goal": GOAL_LABELS[request.goal.goal_id],
                    "approved_slots": request.goal.approved_slots,
                    "verified_evidence": list(evidence),
                },
                basis=basis,
                deadline_ms=1000,
                execution=run.for_child(),
            )
        )
        run.child(model)
        if model.data is None:
            return run.propagate(model, "N-14")
        arrived = approved_selection(model.data, {"ARRIVED"}) is not None
        arrival_key = f"{basis.help_id}:{basis.mode_revision}:{basis.goal_revision}:{basis.screen_revision}"
        return run.finish(
            "arrival_suspected" if arrived else "not_arrived",
            AssessPostCallArrivalOutput(
                arrived=arrived,
                evidence_codes=evidence,
                confidence=model.data.probabilities.get("ARRIVED"),
                arrival_id=uuid5(NAMESPACE_URL, arrival_key).hex if arrived else None,
            ),
        )
