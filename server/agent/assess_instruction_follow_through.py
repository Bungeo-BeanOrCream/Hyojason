"""N-28. 통화 중 부모가 자녀 지시대로 조작했는지 판정한다.

입력은 현재 지시, 조작 전후 화면, 확인된 대상(있을 경우), Basis다.
출력은 CORRECT / WRONG / UNKNOWN 판정과 화면 버전·근거다.
화면 변화가 확인될 때 N-14에 묻고, 관찰 공백이나 모호한 변화는 미확인으로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..contracts import (
    Basis,
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
from .profiles import TARGETS, is_stable, target_profile

Verdict = Literal["CORRECT", "WRONG", "UNKNOWN"]


@dataclass(frozen=True, kw_only=True)
class AssessInstructionFollowThroughInput:
    mode: Mode
    instruction: Instruction
    before: ScreenState
    after: ScreenState
    target: TargetDecision | None
    basis: Basis
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class ActionVerdict:
    verdict: Verdict
    instruction_revision: int
    from_screen_revision: int
    to_screen_revision: int
    evidence_codes: tuple[str, ...]
    model_revision: str | None


@dataclass(kw_only=True)
class AssessInstructionFollowThrough:
    infer_zoro_choice: ZoroChoiceRunner
    node_id = "N-28"

    def run(
        self, request: AssessInstructionFollowThroughInput
    ) -> NodeResult[ActionVerdict]:
        run = NodeRun(self.node_id, request.basis, request.execution)
        before, after, basis = request.before, request.after, request.basis
        if request.mode != "CALL_CHILD_LED":
            return run.fail("NOT_APPLICABLE", "validate")
        if (
            after.projection_epoch != basis.projection_epoch
            or after.screen_revision != basis.screen_revision
            or request.instruction.instruction_revision != basis.instruction_revision
        ):
            return run.fail("STALE_BASIS", "validate")
        run.ok("validate")
        target_id = request.instruction.target_key
        if (
            not target_id
            or target_id not in TARGETS
            or request.instruction.action != "TAP"
            or not is_stable(before)
            or not is_stable(after)
            or before.projection_epoch != after.projection_epoch
            or before.screen_revision >= after.screen_revision
            or (
                request.instruction.origin_screen_revision is not None
                and before.screen_revision < request.instruction.origin_screen_revision
            )
        ):
            run.skip("model", "NO_VERIFIED_TRANSITION")
            return run.finish("not_established", self._output(request, "UNKNOWN"))
        observed = _observed_target(before, after)
        if observed is None or target_profile(target_id, before.screen_kind) is None:
            run.skip("model", "NO_VERIFIED_TRANSITION")
            return run.finish("not_established", self._output(request, "UNKNOWN"))

        expected: Verdict = "CORRECT" if observed == target_id else "WRONG"
        choices = (
            Choice(choice_id="CORRECT", description="화면 전이가 지시된 조작과 일치"),
            Choice(
                choice_id="WRONG", description="화면 전이가 확인된 다른 조작의 결과"
            ),
            Choice(
                choice_id="UNKNOWN", description="증거로 일치 여부를 확인할 수 없음"
            ),
        )
        model = self.infer_zoro_choice(
            InferZoroChoiceInput(
                mode=request.mode,
                question_kind="ACTION_MATCH",
                choices=choices,
                context={
                    "expected_target": TARGETS[target_id].label,
                    "observed_target": TARGETS[observed].label,
                    "before_screen": before.screen_kind,
                    "after_screen": after.screen_kind,
                },
                basis=basis,
                deadline_ms=1000,
                execution=run.for_child(),
            )
        )
        run.child(model)
        if model.data is None:
            return run.propagate(model, "N-14")
        verdict: Verdict = (
            expected if approved_selection(model.data, {expected}) else "UNKNOWN"
        )
        outcome = {
            "CORRECT": "followed",
            "WRONG": "wrong_action",
            "UNKNOWN": "not_established",
        }
        return run.finish(
            outcome[verdict],
            self._output(
                request, verdict, (f"observed:{observed}",), model.data.model_revision
            ),
        )

    @staticmethod
    def _output(
        request: AssessInstructionFollowThroughInput,
        verdict: Verdict,
        evidence: tuple[str, ...] = (),
        revision: str | None = None,
    ) -> ActionVerdict:
        return ActionVerdict(
            verdict=verdict,
            instruction_revision=request.instruction.instruction_revision,
            from_screen_revision=request.before.screen_revision,
            to_screen_revision=request.after.screen_revision,
            evidence_codes=evidence,
            model_revision=revision,
        )


def _observed_target(before: ScreenState, after: ScreenState) -> str | None:
    explicit = set(after.evidence_codes) - set(before.evidence_codes)
    matches = []
    for target_id in before.target_ids:
        profile = target_profile(target_id, before.screen_kind)
        if not profile or profile.action != "TAP":
            continue
        if f"activated:{target_id}" in explicit and (
            profile.destination is None or profile.destination == after.screen_kind
        ):
            matches.append(target_id)
        elif (
            before.screen_kind != after.screen_kind
            and profile.destination == after.screen_kind
        ):
            matches.append(target_id)
    return matches[0] if len(matches) == 1 else None
