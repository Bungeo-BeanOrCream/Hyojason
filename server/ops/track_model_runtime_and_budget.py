"""N-26. Zoro-1 실행 환경과 NVIDIA 크레딧 사용액을 점검한다.

입력은 GPU 종류·비용·잔액, 모델 버전과 실제 호출 결과(준비된 경우)다.
출력은 모델 준비 여부, 실제 호출 통과 수, 누적 비용, 예산 상태다.
초기에는 접근 권한과 GPU를 확인한다. 이후 N-14의 실제 판단 5종으로 준비 여부를 판정한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
import re
from typing import Final, Literal

from ..agent._execution import ExecutionContext, NodeRun
from ..agent.infer_zoro_choice import QuestionKind
from ..contracts import Basis, NodeResult, ResultRef, Source, StepDiagnostic

NVIDIA_CREDIT_CAP_CENTS: Final = 10_000
EXPERIMENT_STOP_CENTS: Final = 7_000
MODEL_QUESTIONS: Final[tuple[QuestionKind, ...]] = (
    "GOAL",
    "TARGET_LOCATION",
    "ACTION_MATCH",
    "NEXT_ACTION",
    "ARRIVAL",
)
BudgetState = Literal["within_budget", "hold", "exhausted"]
RequestStatus = Literal["SUCCESS", "TIMEOUT", "ERROR", "PATH_MISSING"]
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:#/+~-]{0,127}\Z")
_PHONE_LIKE = re.compile(r"(?:\+?[0-9][-.]?){7,}\Z")


@dataclass(frozen=True, kw_only=True)
class DecisionCheck:
    question_kind: QuestionKind
    model_revision: str
    succeeded: bool
    latency_ms: int
    inference_source: Source
    probability: float | None = None
    request_status: RequestStatus = "SUCCESS"
    result_ref: ResultRef | None = None


@dataclass(frozen=True, kw_only=True)
class TrackModelRuntimeAndBudgetInput:
    model_revision: str
    gpu_sku: str
    spend_cents: int
    smoke_results: tuple[DecisionCheck, ...] = ()
    credit_cents: int = NVIDIA_CREDIT_CAP_CENTS
    experiments_paused_at_70: bool | None = None


@dataclass(frozen=True, kw_only=True)
class TrackModelRuntimeAndBudgetOutput:
    model_ready: bool
    model_revision: str
    smoke_passes: int
    spend_cents: int
    budget_state: BudgetState


def _token(value: str) -> bool:
    return (
        isinstance(value, str)
        and _TOKEN.fullmatch(value) is not None
        and _PHONE_LIKE.fullmatch(value) is None
        and "://" not in value
    )


def budget_state(request: TrackModelRuntimeAndBudgetInput) -> BudgetState:
    """판정에 앞서 새 실험을 시작할 수 있는지 확인한다."""
    if (
        type(request.spend_cents) is not int
        or request.spend_cents < 0
        or type(request.credit_cents) is not int
        or not 0 < request.credit_cents <= NVIDIA_CREDIT_CAP_CENTS
        or request.experiments_paused_at_70 is not None
        and type(request.experiments_paused_at_70) is not bool
    ):
        raise ValueError("invalid budget ledger")
    if request.spend_cents >= request.credit_cents:
        return "exhausted"
    if request.spend_cents >= EXPERIMENT_STOP_CENTS:
        return "hold"
    return "within_budget"


def _valid_check(check: DecisionCheck) -> bool:
    probability = check.probability
    return (
        check.question_kind in MODEL_QUESTIONS
        and _token(check.model_revision)
        and type(check.succeeded) is bool
        and type(check.latency_ms) is int
        and check.latency_ms >= 0
        and check.inference_source in ("LIVE", "FIXTURE")
        and check.request_status in ("SUCCESS", "TIMEOUT", "ERROR", "PATH_MISSING")
        and (not check.succeeded or check.request_status == "SUCCESS")
        and (
            probability is None
            or type(probability) in (int, float)
            and 0 <= probability <= 1
            and isfinite(probability)
        )
    )


def _checks(
    request: TrackModelRuntimeAndBudgetInput,
) -> dict[QuestionKind, DecisionCheck]:
    if not _token(request.model_revision) or not _token(request.gpu_sku):
        raise ValueError("invalid model revision or GPU SKU")
    checks: dict[QuestionKind, DecisionCheck] = {}
    for check in request.smoke_results:
        if not _valid_check(check) or check.question_kind in checks:
            raise ValueError("invalid or duplicate model question")
        checks[check.question_kind] = check
    return checks


class TrackModelRuntimeAndBudget:
    node_id = "N-26"

    def run(
        self,
        request: TrackModelRuntimeAndBudgetInput,
        *,
        execution: ExecutionContext | None = None,
        basis: Basis | None = None,
    ) -> NodeResult[TrackModelRuntimeAndBudgetOutput]:
        run = NodeRun(self.node_id, basis or Basis(), execution)
        try:
            spending = budget_state(request)
            checks = _checks(request)
        except ValueError:
            return run.fail("INVALID_INPUT", "validate")
        run.ok("validate")
        input_fixture = run.source == "FIXTURE"
        if input_fixture or any(
            check.inference_source == "FIXTURE" for check in checks.values()
        ):
            run.source = "FIXTURE"
        run.children.extend(
            check.result_ref
            for check in checks.values()
            if check.result_ref is not None
        )
        if request.spend_cents > request.credit_cents:
            return run.fail("CREDIT_CAP_EXCEEDED", "budget")
        run.ok("budget", code=spending.upper())
        if any(
            check.model_revision != request.model_revision for check in checks.values()
        ):
            return run.fail("STALE_BASIS", "model_revision")

        passes = 0
        failure_codes: set[str] = set()
        for question in MODEL_QUESTIONS:
            step = f"smoke_{question.lower()}"
            check = checks.get(question)
            if check is None or check.request_status == "PATH_MISSING":
                run.skip(step, "MISSING_MODEL_CHECK")
            elif check.inference_source == "FIXTURE" or input_fixture:
                run.skip(step, "FIXTURE_MODEL_CHECK")
            elif check.request_status != "SUCCESS":
                code = (
                    "TIMEOUT"
                    if check.request_status == "TIMEOUT"
                    else "DEPENDENCY_UNAVAILABLE"
                )
                run.steps[step] = StepDiagnostic(status="failed", code=code)
                failure_codes.add(code)
            elif check.probability is None:
                run.skip(step, "MISSING_MODEL_TELEMETRY")
            elif not check.succeeded:
                run.steps[step] = StepDiagnostic(
                    status="failed", code="UNVERIFIED_MODEL_CHOICE"
                )
                failure_codes.add("UNVERIFIED_MODEL_CHOICE")
            else:
                passes += 1
                run.ok(step)

        if passes != len(MODEL_QUESTIONS):
            code = "DEPENDENCY_UNAVAILABLE"
            if "TIMEOUT" in failure_codes:
                code = "TIMEOUT"
            elif failure_codes == {"UNVERIFIED_MODEL_CHOICE"}:
                code = "UNVERIFIED_MODEL_CHOICE"
            return run.fail(code, "smoke", retryable=code != "UNVERIFIED_MODEL_CHOICE")

        output = TrackModelRuntimeAndBudgetOutput(
            model_ready=True,
            model_revision=request.model_revision,
            smoke_passes=passes,
            spend_cents=request.spend_cents,
            budget_state=spending,
        )
        if spending != "within_budget":
            return run.finish("budget_hold", output, status="partial")
        return run.finish("runtime_ready", output)


def v08_evidence(
    request: TrackModelRuntimeAndBudgetInput,
    *,
    execution: ExecutionContext,
    basis: Basis | None = None,
) -> dict[str, list[dict[str, object]]]:
    """D 브랜치 N-25의 observed_runs 파일에 넣을 V-08 증거를 만든다."""
    budget_state(request)
    checks = _checks(request)
    if not _token(execution.trace_id) or execution.source not in ("LIVE", "FIXTURE"):
        raise ValueError("invalid evidence context")
    common: dict[str, object] = {
        "attempt": 1,
        "trace_id": execution.trace_id,
        "basis": asdict(basis or Basis()),
    }
    rows: list[dict[str, object]] = []
    for question in MODEL_QUESTIONS:
        check = checks.get(question)
        if check is None or check.request_status == "PATH_MISSING":
            continue
        source = "FIXTURE" if check.inference_source == "FIXTURE" else execution.source
        rows.append(
            {
                **common,
                "case_id": f"V08-{question}",
                "source": source,
                "model_source": check.inference_source,
                "model_revision": check.model_revision,
                "actual": {
                    "question_type": question,
                    "gpu": request.gpu_sku,
                    "probability": check.probability,
                    "latency_ms": check.latency_ms,
                    "choice_verified": check.succeeded,
                    "request_status": check.request_status,
                },
            }
        )
    # N-25's credits_cents is the cumulative recorded spend. A hold
    # recommendation does not prove the operator stopped experiments.
    ledger: dict[str, int | bool] = {"credits_cents": request.spend_cents}
    if request.experiments_paused_at_70 is not None:
        ledger["experiments_paused_at_70"] = request.experiments_paused_at_70
    rows.append(
        {
            **common,
            "case_id": "V08-budget",
            "source": execution.source,
            "actual": ledger,
        }
    )
    return {"observed_runs": rows}
