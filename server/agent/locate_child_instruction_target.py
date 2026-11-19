"""
N-27. 통화 중 자녀가 말한 버튼의 위치를 확인한다.

입력은 현재 지시, 검증된 후보 집합, 화면 요약, Basis다.
출력은 위치 분류(보임, 화면 아래, 미확인, 차단), 대상 ID, 근거다.
N-14로 허용 대상을 선택한다. 실제 좌표는 부모 앱이 화면에서 다시 찾는다.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..contracts import (
    Basis,
    CandidateSet,
    Instruction,
    Mode,
    NodeResult,
    ScreenState,
    TargetDecision,
)
from ._execution import ExecutionContext, NodeRun
from .infer_zoro_choice import (
    Choice,
    InferZoroChoiceInput,
    ZoroChoiceRunner,
    approved_selection,
)
from .profiles import below_fold_evidence, is_protected, is_stable, target_profile


@dataclass(frozen=True, kw_only=True)
class LocateChildInstructionTargetInput:
    mode: Mode
    instruction: Instruction
    candidate_set: CandidateSet
    screen: ScreenState
    basis: Basis
    execution: ExecutionContext | None = None


@dataclass(kw_only=True)
class LocateChildInstructionTarget:
    infer_zoro_choice: ZoroChoiceRunner
    node_id = "N-27"

    def run(
        self, request: LocateChildInstructionTargetInput
    ) -> NodeResult[TargetDecision]:
        screen, basis = request.screen, request.basis
        run = NodeRun(self.node_id, basis, request.execution)
        if request.mode != "CALL_CHILD_LED":
            return run.fail("NOT_APPLICABLE", "validate")
        if (
            screen.projection_epoch != basis.projection_epoch
            or screen.screen_revision != basis.screen_revision
            or request.instruction.instruction_revision != basis.instruction_revision
        ):
            return run.fail("STALE_BASIS", "validate")
        run.ok("validate")
        if is_protected(screen) or request.candidate_set.coverage == "policy_blocked":
            return run.finish("policy_denied", self._no_target("blocked"))
        target = request.instruction.target_key
        profile = target_profile(target, screen.screen_kind) if target else None
        if not is_stable(screen) or not profile or request.instruction.action != "TAP":
            return run.finish("target_unknown", self._no_target("unknown"))

        eligible = {}
        for candidate in request.candidate_set.candidates:
            if candidate.target_id != target:
                continue
            if candidate.action == "TAP" and target in screen.target_ids:
                eligible[candidate.choice_id] = candidate
            if (
                candidate.action == "SCROLL_DOWN"
                and request.candidate_set.coverage == "complete"
                and target
                and below_fold_evidence(screen, target)
            ):
                eligible[candidate.choice_id] = candidate
        if not eligible:
            run.skip("model", "NO_CANDIDATES")
            return run.finish("target_unknown", self._no_target("unknown"))

        choices = tuple(
            Choice(
                choice_id=choice_id,
                description=f"{profile.label} ({'현재 화면' if candidate.action == 'TAP' else '현재 영역 아래'})",
            )
            for choice_id, candidate in eligible.items()
        )
        response = self.infer_zoro_choice(
            InferZoroChoiceInput(
                mode=request.mode,
                question_kind="TARGET_LOCATION",
                choices=(
                    *choices,
                    Choice(choice_id="UNKNOWN", description="현재 확실히 찾을 수 없음"),
                ),
                context={
                    "instructed_target": profile.label,
                    "screen_kind": screen.screen_kind,
                },
                basis=basis,
                deadline_ms=1000,
                execution=run.for_child(),
            )
        )
        run.child(response)
        if response.data is None:
            return run.propagate(response, "N-14")
        pick = approved_selection(response.data, set(eligible))
        if pick is None:
            return run.finish(
                "target_unknown",
                self._no_target("unknown", response.data.model_revision),
            )
        selected = eligible[pick]
        visible = selected.action == "TAP"
        return run.finish(
            "target_visible" if visible else "target_below_fold",
            TargetDecision(
                location_kind="visible" if visible else "below_fold",
                target_id=selected.target_id,
                anchor_screen_revision=screen.screen_revision,
                evidence_codes=selected.evidence_codes,
                model_revision=response.data.model_revision,
            ),
        )

    @staticmethod
    def _no_target(
        kind: Literal["unknown", "blocked"], revision: str | None = None
    ) -> TargetDecision:
        return TargetDecision(
            location_kind=kind,
            target_id=None,
            anchor_screen_revision=None,
            evidence_codes=(),
            model_revision=revision,
        )
