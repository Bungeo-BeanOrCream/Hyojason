"""N-14를 통해 Zoro-1의 5종 판단을 점검하고 N-25 증거를 저장한다."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from uuid import uuid4

from ..agent._execution import ExecutionContext
from ..agent.infer_zoro_choice import (
    Choice,
    InferZoroChoice,
    InferZoroChoiceInput,
    QuestionKind,
    ZoroChoiceRunner,
    approved_selection,
)
from ..contracts import Basis, JsonValue, Mode
from .track_model_runtime_and_budget import (
    NVIDIA_CREDIT_CAP_CENTS,
    DecisionCheck,
    TrackModelRuntimeAndBudget,
    TrackModelRuntimeAndBudgetInput,
    budget_state,
    v08_evidence,
)


@dataclass(frozen=True, kw_only=True)
class _SmokeCase:
    mode: Mode
    question: QuestionKind
    expected_id: str
    context: dict[str, JsonValue]
    choices: dict[str, str]


_CASES = (
    _SmokeCase(
        mode="CALL_CHILD_LED",
        question="GOAL",
        expected_id="CLOCK_ALARM",
        context={"instruction": "내일 오전 6시에 알람을 맞춰 줘"},
        choices={
            "CLOCK_ALARM": "정해진 시각의 알람 맞추기",
            "NONE": "명시적 목표 없음",
        },
    ),
    _SmokeCase(
        mode="CALL_CHILD_LED",
        question="TARGET_LOCATION",
        expected_id="tap:settings.display",
        context={
            "instruction": "디스플레이를 눌러 줘",
            "screen_kind": "settings_home",
            "visible_target_ids": ["settings.display"],
        },
        choices={
            "UNKNOWN": "확인 불가",
            "tap:settings.display": "보이는 디스플레이 버튼",
        },
    ),
    _SmokeCase(
        mode="CALL_CHILD_LED",
        question="ACTION_MATCH",
        expected_id="CORRECT",
        context={
            "expected_target": "디스플레이",
            "observed_target": "디스플레이",
            "before_screen": "settings_home",
            "after_screen": "settings_display",
        },
        choices={
            "UNKNOWN": "확인 불가",
            "WRONG": "다른 화면으로 이동",
            "CORRECT": "지시한 화면으로 이동",
        },
    ),
    _SmokeCase(
        mode="POST_CALL_AGENT",
        question="NEXT_ACTION",
        expected_id="tap:clock.add_alarm",
        context={
            "goal": "내일 오전 6시 알람 추가",
            "screen_kind": "clock_alarm_list",
            "visible_target_ids": ["clock.add_alarm"],
        },
        choices={
            "tap:clock.add_alarm": "알람 추가 버튼 누르기",
            "WAIT": "현재 단계 안내 보류",
        },
    ),
    _SmokeCase(
        mode="POST_CALL_AGENT",
        question="ARRIVAL",
        expected_id="ARRIVED",
        context={
            "goal": "내일 오전 6시 알람",
            "approved_slots": {"day": "tomorrow", "time": "06:00"},
            "verified_evidence": ["alarm_card_active", "alarm_next:tomorrow:06:00"],
        },
        choices={
            "NOT_ARRIVED": "필수 완료 근거 부족",
            "ARRIVED": "목표 시각·날짜·활성 상태 확인",
        },
    ),
)


def collect_smoke_checks(
    request: TrackModelRuntimeAndBudgetInput,
    *,
    infer: ZoroChoiceRunner,
    execution: ExecutionContext,
    deadline_ms: int = 1_000,
) -> TrackModelRuntimeAndBudgetInput:
    """현재 비용을 확인하고 각 질문의 최초 시도와 출처를 수집한다."""
    spending = budget_state(request)
    v08_evidence(request, execution=execution)
    if request.smoke_results:
        raise ValueError("기록된 smoke 결과는 별도로 판정해 주세요")
    if type(deadline_ms) is not int or deadline_ms <= 0:
        raise ValueError("요청 제한 시간은 양수여야 합니다")
    if spending != "within_budget":
        return request

    checks = []
    for case in _CASES:
        result = infer(
            InferZoroChoiceInput(
                mode=case.mode,
                question_kind=case.question,
                context=case.context.copy(),
                choices=tuple(
                    Choice(choice_id=key, description=value)
                    for key, value in case.choices.items()
                ),
                basis=Basis(mode_revision=1 if case.mode == "CALL_CHILD_LED" else 2),
                deadline_ms=deadline_ms,
                execution=execution,
            )
        )
        output = result.data
        if output is None or result.status != "success":
            error = result.diagnostics.terminal_error
            checks.append(
                DecisionCheck(
                    question_kind=case.question,
                    model_revision=request.model_revision,
                    succeeded=False,
                    latency_ms=result.diagnostics.duration_ms,
                    inference_source=result.diagnostics.source,
                    request_status="TIMEOUT"
                    if error and error.code == "TIMEOUT"
                    else "ERROR",
                    result_ref=result.ref,
                )
            )
            continue
        checks.append(
            DecisionCheck(
                question_kind=case.question,
                model_revision=output.model_revision,
                succeeded=approved_selection(output, {case.expected_id}) is not None,
                latency_ms=output.latency_ms,
                inference_source=output.inference_source,
                probability=output.probabilities.get(output.selected_id),
                result_ref=result.ref,
            )
        )
    return replace(request, smoke_results=tuple(checks))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu-sku", required=True, help="콘솔에서 확인한 GPU SKU, 예: L40S"
    )
    parser.add_argument(
        "--spend-cents",
        required=True,
        type=int,
        help="콘솔에서 확인한 누적 차감액(센트)",
    )
    parser.add_argument("--credit-cents", default=NVIDIA_CREDIT_CAP_CENTS, type=int)
    parser.add_argument("--deadline-ms", default=1_000, type=int)
    parser.add_argument(
        "--experiments-paused-at-70", action=argparse.BooleanOptionalAction
    )
    parser.add_argument("--evidence-json", required=True, type=Path)
    args = parser.parse_args(argv)

    model = InferZoroChoice.from_environment()
    if not model.endpoint or not model.model_revision:
        parser.error("ZORO_DECIDE_URL과 ZORO_MODEL_REVISION을 설정해 주세요")
    execution = ExecutionContext(trace_id=f"v08-{uuid4().hex}", source="LIVE")
    request = TrackModelRuntimeAndBudgetInput(
        model_revision=model.model_revision,
        gpu_sku=args.gpu_sku,
        spend_cents=args.spend_cents,
        credit_cents=args.credit_cents,
        experiments_paused_at_70=args.experiments_paused_at_70,
    )
    if args.evidence_json.exists():
        parser.error(
            "기존 최초 시도 증거를 보존하려면 새 증거 파일 경로를 지정해 주세요"
        )
    try:
        request = collect_smoke_checks(
            request, infer=model.run, execution=execution, deadline_ms=args.deadline_ms
        )
        decision = TrackModelRuntimeAndBudget().run(request, execution=execution)
        evidence = v08_evidence(request, execution=execution)
        with args.evidence_json.open("x", encoding="utf-8") as output:
            json.dump(evidence, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write("\n")
    except (ValueError, OSError) as exc:
        parser.error(
            f"비용 입력 또는 증거 파일을 처리할 수 없습니다 ({type(exc).__name__})"
        )

    print(
        json.dumps(
            {"budget_state": budget_state(request), "result": asdict(decision)},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return 0 if decision.status == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
