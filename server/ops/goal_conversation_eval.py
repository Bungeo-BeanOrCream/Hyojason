"""V-05 대화형 목표 표본을 N-15에 차례로 넣어 Zoro-1의 목표 확률·변경을 평가한다.

세션 서버(N-11)가 하는 것처럼 발화마다 최근 확정 발화 창, 직전 GoalBelief, 현재 목표를
이어 붙인다. 결과에는 케이스·발화 순번·선택·확률·지연·출처만 남기고 발화 원문은 남기지 않는다.
실모델 호출은 N-26과 같은 비용 기준을 따른다: 누적 $70 이상이면 새 요청을 보내지 않는다.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

from ..agent._execution import ExecutionContext
from ..agent.infer_zoro_choice import InferZoroChoice, InferZoroChoiceInput, ZoroChoiceRunner
from ..agent.interpret_child_turn import (
    RECENT_UTTERANCE_LIMIT,
    InterpretChildTurn,
    InterpretChildTurnInput,
)
from ..agent.profiles import POLICY_REVISION
from ..contracts import Basis, FinalUtterance, Goal, GoalBelief, NodeResult, Source
from .track_model_runtime_and_budget import (
    NVIDIA_CREDIT_CAP_CENTS,
    TrackModelRuntimeAndBudgetInput,
    budget_state,
)

DEFAULT_SAMPLES = Path(__file__).resolve().parents[2] / "demo" / "fixtures" / "goal_conversations.json"


@dataclass(frozen=True, kw_only=True)
class TurnRecord:
    turn: int
    speaker: str
    model_called: bool
    selected_id: str | None
    probability: float | None
    latency_ms: int | None
    inference_source: str | None
    error: str | None
    goal_after: str | None
    expected_goal: str | None

    @property
    def matched(self) -> bool:
        return self.goal_after == self.expected_goal


@dataclass(frozen=True, kw_only=True)
class CaseRecord:
    case_id: str
    turns: tuple[TurnRecord, ...]
    final_goal: dict[str, Any] | None
    expected_final: dict[str, Any] | None

    @property
    def final_matched(self) -> bool:
        return self.final_goal == self.expected_final


@dataclass
class _Recorder:
    """N-15가 부르는 N-14 결과를 그대로 넘기면서 GOAL 호출만 기록한다."""

    infer: ZoroChoiceRunner
    calls: list[NodeResult] = field(default_factory=list)

    def __call__(self, request: InferZoroChoiceInput) -> NodeResult:
        result = self.infer(request)
        if request.question_kind == "GOAL":
            self.calls.append(result)
        return result


def load_samples(path: Path = DEFAULT_SAMPLES) -> list[dict[str, Any]]:
    samples = json.loads(path.read_text(encoding="utf-8"))
    for sample in samples:
        if len(sample["turns"]) != len(sample["expected_goals"]):
            raise ValueError(f"{sample['id']}: turns와 expected_goals 길이가 다릅니다")
        if sample["assisted_role"] not in ("PARENT", "CHILD"):
            raise ValueError(f"{sample['id']}: assisted_role")
    return samples


def evaluate_case(
    sample: dict[str, Any], *, infer: ZoroChoiceRunner, source: Source
) -> CaseRecord:
    """한 대화를 N-11과 같은 상태 전달 규칙으로 재생한다. GOAL 마감은 N-15의 1초를 쓴다."""
    case_id, assisted = sample["id"], sample["assisted_role"]
    helper = "CHILD" if assisted == "PARENT" else "PARENT"
    interpret = InterpretChildTurn(infer_zoro_choice=infer)
    recorder = infer if isinstance(infer, _Recorder) else None
    goal: Goal | None = None
    belief: GoalBelief | None = None
    window: list[FinalUtterance] = []
    seqs = {"PARENT": 0, "CHILD": 0}
    helper_seq: int | None = None
    records = []
    for index, (turn, expected) in enumerate(zip(sample["turns"], sample["expected_goals"]), start=1):
        role = turn["role"]
        seqs[role] += 1
        if role == helper:
            helper_seq = seqs[role]
        # N-15는 지시하는 쪽을 child, 지원 대상을 parent로 받는다.
        speech = FinalUtterance(
            utterance_id=f"{case_id}-{index}", utterance_seq=seqs[role],
            speaker_role="CHILD" if role == helper else "PARENT",
            text=turn["text"], call_id=case_id, source=source,
        )
        basis = Basis(
            pair_id="v05", call_id=case_id, help_id=f"{case_id}-help", consent_revision=1,
            mode_revision=0, instruction_revision=0,
            goal_revision=goal.goal_revision if goal else 0, utterance_seq=helper_seq,
        )
        before = len(recorder.calls) if recorder else 0
        result = interpret.run(InterpretChildTurnInput(
            mode="CALL_CHILD_LED", speech=speech, existing_goal=goal,
            goal_catalog_revision=POLICY_REVISION, basis=basis,
            recent_utterances=tuple(window), goal_belief=belief, assisted_role=assisted,
            execution=ExecutionContext(trace_id=f"v05-{case_id}", source=source),
        ))
        window = [*window, speech][-RECENT_UTTERANCE_LIMIT:]
        call = recorder.calls[-1] if recorder and len(recorder.calls) > before else None
        data = result.data
        if data and data.goal_belief:
            belief = data.goal_belief
        if data and data.goal_draft:
            draft = data.goal_draft
            goal = Goal(goal_id=draft.goal_id, goal_revision=(goal.goal_revision if goal else 0) + 1,
                        approved_slots=dict(draft.approved_slots), source_utterance_id=draft.source_utterance_id)
        output = call.data if call else None
        error = call.diagnostics.terminal_error if call else None
        records.append(TurnRecord(
            turn=index, speaker=role, model_called=call is not None,
            selected_id=output.selected_id if output else None,
            probability=output.probabilities.get(output.selected_id) if output else None,
            latency_ms=output.latency_ms if output else (call.diagnostics.duration_ms if call else None),
            inference_source=output.inference_source if output else (call.diagnostics.source if call else None),
            error=error.code if error else None,
            goal_after=goal.goal_id if goal else None, expected_goal=expected,
        ))
    final = {"goal_id": goal.goal_id, "approved_slots": dict(goal.approved_slots)} if goal else None
    return CaseRecord(case_id=case_id, turns=tuple(records), final_goal=final,
                      expected_final=sample["expected_final"])


def evaluate(
    samples: Sequence[dict[str, Any]], *, infer: ZoroChoiceRunner, source: Source
) -> list[CaseRecord]:
    recorder = _Recorder(infer)
    return [evaluate_case(sample, infer=recorder, source=source) for sample in samples]


def summarize(cases: Sequence[CaseRecord]) -> dict[str, Any]:
    turns = [turn for case in cases for turn in case.turns]
    called = [turn for turn in turns if turn.model_called]
    latencies = sorted(turn.latency_ms for turn in called if turn.latency_ms is not None and not turn.error)
    p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))] if latencies else None
    sources = sorted({turn.inference_source for turn in called if turn.inference_source})
    return {
        "cases": len(cases),
        "final_goal_matched": sum(case.final_matched for case in cases),
        "turns": len(turns),
        "turn_goal_matched": sum(turn.matched for turn in turns),
        "goal_calls": len(called),
        "timeouts": sum(turn.error == "TIMEOUT" for turn in called),
        "errors": sum(turn.error is not None for turn in called),
        "latency_p50_ms": median(latencies) if latencies else None,
        "latency_p95_ms": p95,
        "inference_sources": sources,
        "failed_cases": [case.case_id for case in cases if not case.final_matched
                         or not all(turn.matched for turn in case.turns)],
    }


def evidence(cases: Sequence[CaseRecord], *, model_revision: str | None, trace_id: str) -> dict[str, Any]:
    """공유용 결과. 발화 원문 없이 케이스·순번·선택·확률·지연·출처만 담는다."""
    return {
        "trace_id": trace_id,
        "model_revision": model_revision,
        "summary": summarize(cases),
        "cases": [
            {"case_id": case.case_id, "final_matched": case.final_matched,
             "final_goal": case.final_goal, "expected_final": case.expected_final,
             "turns": [{"turn": t.turn, "speaker": t.speaker, "model_called": t.model_called,
                        "selected_id": t.selected_id, "probability": t.probability,
                        "latency_ms": t.latency_ms, "inference_source": t.inference_source,
                        "error": t.error, "goal_after": t.goal_after, "expected_goal": t.expected_goal,
                        "matched": t.matched} for t in case.turns]}
            for case in cases
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spend-cents", required=True, type=int, help="콘솔에서 확인한 누적 차감액(센트)")
    parser.add_argument("--credit-cents", default=NVIDIA_CREDIT_CAP_CENTS, type=int)
    parser.add_argument("--samples", default=DEFAULT_SAMPLES, type=Path)
    parser.add_argument("--evidence-json", required=True, type=Path)
    args = parser.parse_args(argv)

    model = InferZoroChoice.from_environment()
    if not model.endpoint or not model.model_revision:
        parser.error("ZORO_DECIDE_URL과 ZORO_MODEL_REVISION을 설정해 주세요")
    if args.evidence_json.exists():
        parser.error("기존 최초 시도 증거를 보존하려면 새 증거 파일 경로를 지정해 주세요")
    ledger = TrackModelRuntimeAndBudgetInput(
        model_revision=model.model_revision, gpu_sku="L40S",
        spend_cents=args.spend_cents, credit_cents=args.credit_cents,
    )
    try:
        state = budget_state(ledger)
    except ValueError:
        parser.error("비용 입력이 올바르지 않습니다")
    if state != "within_budget":
        print(json.dumps({"budget_state": state, "requests_sent": 0}, ensure_ascii=False))
        return 2
    trace_id = f"v05-{uuid4().hex}"
    cases = evaluate(load_samples(args.samples), infer=model.run, source="LIVE")
    result = evidence(cases, model_revision=model.model_revision, trace_id=trace_id)
    with args.evidence_json.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
    print(json.dumps({"budget_state": state, **result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
