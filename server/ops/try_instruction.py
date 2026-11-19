"""자녀 발화를 직접 넣어 Zoro-1의 목표 판단(N-15 → N-14)만 확인하는 개인 테스트용 스크립트.

통화·STT·서버 없이, 입력한 문장을 '자녀가 말한 확정 발화'로 보고 실제 앱과 같은
InterpretChildTurn 경로로 Zoro-1을 호출한다. 여러 줄을 입력하면 앞 발화가 대화 창과
직전 목표 확률로 다음 판단에 넘어간다(세션 서버와 같은 방식).

실행(저장소 루트):
  python3.11 -m server.ops.try_instruction              # 대화형, 실제 Zoro-1
  python3.11 -m server.ops.try_instruction "글자 좀 키워줘"
  python3.11 -m server.ops.try_instruction --fake       # 모델 없이 스크립트 동작만 확인(FIXTURE)
필요한 환경 변수(실제 모델): ZORO_DECIDE_URL, ZORO_MODEL_REVISION, (프록시면) ZORO_DECIDE_TOKEN
주의: 실제 모델 모드는 입력 한 줄마다 Zoro-1을 한 번 호출한다(크레딧 차감).
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import sys
from uuid import uuid4

from ..agent._execution import ExecutionContext
from ..agent.infer_zoro_choice import InferZoroChoice
from ..agent.interpret_child_turn import InterpretChildTurn, InterpretChildTurnInput
from ..agent.profiles import GOAL_LABELS, POLICY_REVISION
from ..contracts import Basis, FinalUtterance, Goal

_FAKE_KEYWORDS = {
    "SET_FONT": ("글자", "글씨", "폰트"),
    "SET_TIMEOUT": ("꺼짐", "화면 시간", "10분"),
    "CLOCK_ALARM": ("알람",),
    "MAP_ROUTE": ("길찾기", "지도"),
    "SMS_SEND": ("문자",),
    "MAIL_OPEN": ("메일",),
}


def _fake_transport(payload, timeout):
    """모델 없이 응답 형식만 흉내 낸다. 판단 품질과 무관하다."""
    question = payload["request"]["questions"]["decision"]
    ids = list(question["criteria"])
    turns = payload["request"]["state"].get("conversation") or [{}]
    text = turns[-1].get("text", "")  # 가짜 응답은 마지막 발화만 본다
    pick = next((g for g, words in _FAKE_KEYWORDS.items() if g in ids and any(w in text for w in words)), "NONE")
    rest = (1 - 0.9) / (len(ids) - 1)
    probs = {i: (0.9 if i == pick else rest) for i in ids}
    return {"answers": {"decision": {"type": "choice", "choice": pick, "probabilities": probs}}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", nargs="*", help="자녀 발화. 없으면 대화형으로 입력")
    parser.add_argument("--fake", action="store_true", help="실제 모델 대신 FIXTURE 응답")
    parser.add_argument("--deadline-ms", type=int, default=1000,
                        help="모델 호출 제한 시간(앱 기본 1000). 느린 서버 확인용")
    parser.add_argument("--assisted", choices=["PARENT", "CHILD"], default="PARENT",
                        help="도움 받는 쪽(기본: 부모 화면을 안내)")
    parser.add_argument("--show-payload", action="store_true", help="Zoro-1에 보낸 state를 출력")
    args = parser.parse_args(argv)

    if args.fake:
        model = InferZoroChoice(model_revision="fixture", transport=_fake_transport)
    else:
        model = InferZoroChoice.from_environment()
        if not model.endpoint or not model.model_revision:
            parser.error("ZORO_DECIDE_URL과 ZORO_MODEL_REVISION을 설정하거나 --fake로 실행하세요")

    sent = []

    def runner(request):
        request = replace(request, deadline_ms=args.deadline_ms)
        sent.append(request)
        return model.run(request)

    node = InterpretChildTurn(infer_zoro_choice=runner)
    source = "FIXTURE" if args.fake else "LIVE"
    execution = ExecutionContext(trace_id=f"try-{uuid4().hex[:8]}", source=source)
    history: list[FinalUtterance] = []
    belief = None
    goal: Goal | None = None

    lines = [" ".join(args.text)] if args.text else None
    if lines is None:
        print("자녀 발화를 입력하세요 (빈 줄/Ctrl-D 종료). 모델:", "FIXTURE" if args.fake else model.endpoint)
    seq = 0
    while True:
        if lines is not None:
            if not lines:
                break
            text = lines.pop(0)
        else:
            try:
                text = input("자녀> ").strip()
            except EOFError:
                break
        if not text:
            break
        seq += 1
        speech = FinalUtterance(utterance_id=f"u{seq}", utterance_seq=seq, speaker_role="CHILD",
                                text=text, call_id="try-call", source=source)
        basis = Basis(call_id="try-call", utterance_seq=seq, mode_revision=1,
                      goal_revision=goal.goal_revision if goal else None)
        sent.clear()
        result = node.run(InterpretChildTurnInput(
            mode="CALL_CHILD_LED", speech=speech, existing_goal=goal,
            goal_catalog_revision=POLICY_REVISION, basis=basis,
            recent_utterances=tuple(history), goal_belief=belief,
            assisted_role=args.assisted, execution=execution,
        ))
        history.append(speech)
        _report(result, sent, args.show_payload)
        data = result.data
        if data and data.goal_belief:
            belief = data.goal_belief
        if data and data.goal_draft:
            d = data.goal_draft
            goal = Goal(goal_id=d.goal_id, goal_revision=(goal.goal_revision + 1 if goal else 1),
                        approved_slots=d.approved_slots, source_utterance_id=d.source_utterance_id)
    return 0


def _report(result, sent, show_payload) -> None:
    print(f"  결과: status={result.status} outcome={result.outcome}")
    err = result.diagnostics.terminal_error
    if err:
        print(f"  오류: {err.code} (단계 {err.step})")
    data = result.data
    if data and data.instruction_draft:
        d = data.instruction_draft
        print(f"  [규칙] 버튼 지시 초안: action={d.action} target_key={d.target_key}")
    if data and data.turn_kind == "blocked":
        print("  [규칙] 위험 요청으로 차단 — 모델 호출 안 함")
    if show_payload:
        for r in sent:
            print("  Zoro-1 state:", json.dumps(r.context, ensure_ascii=False))
    if data and data.goal_belief:
        probs = data.goal_belief.probabilities
        top = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        print(f"  [Zoro-1] 목표 확률 (revision {data.goal_belief.model_revision}):")
        for gid, p in top:
            label = GOAL_LABELS.get(gid, "명시적 목표 없음")
            print(f"    {p:6.3f}  {gid:12s} {label}")
        print(f"  [채택 기준 0.70·차이 0.15] 채택:", data.goal_draft.goal_id if data.goal_draft else "없음(기존 목표 유지)")
        if data.goal_draft:
            print(f"    슬롯: {json.dumps(data.goal_draft.approved_slots, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.exit(main())
