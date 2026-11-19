"""발화 → 목표(N-15) → 화면마다 다음 행동(N-16 후보 + N-17 Zoro-1 NEXT_ACTION) 시뮬레이션. 개인 테스트용.

실제 부모 화면 관찰(Android A) 대신, profiles.py의 시연 화면 프로필로 화면 요약(ScreenState)을 만든다.
각 화면에서 Zoro-1이 고른 버튼을 '눌렀다'고 보고 그 버튼의 목적지 화면으로 넘어간다.
화면 좌표·실제 화면 인식은 검증하지 않는다(그건 Android 쪽 담당).

실행(저장소 루트):
  python3 -m server.ops.try_navigation "글자가 너무 작아"
  python3 -m server.ops.try_navigation --fake "글자가 너무 작아"   # 모델 없이 흐름만
주의: 실제 모델 모드는 목표 판단 1회 + 화면마다 1회씩 Zoro-1을 호출한다(크레딧 차감).
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import sys
from uuid import uuid4

from ..agent._execution import ExecutionContext
from ..agent.choose_post_call_action import ChoosePostCallAction, ChoosePostCallActionInput
from ..agent.enumerate_action_candidate import EnumerateActionCandidates, EnumerateActionCandidatesInput
from ..agent.infer_zoro_choice import InferZoroChoice
from ..agent.interpret_child_turn import InterpretChildTurn, InterpretChildTurnInput
from ..agent.profiles import (
    GOAL_LABELS, GOAL_ROUTES, POLICY_REVISION, TARGETS, alarm_token,
    arrival_evidence, required_arrival_evidence,
)
from ..contracts import Basis, FinalUtterance, Goal, NavigationTrail, ScreenState
from .try_instruction import _fake_transport

SCREEN_NAMES = {
    "home": "홈 화면", "settings_home": "설정", "settings_display": "설정 > 디스플레이",
    "settings_display_size_text": "디스플레이 > 표시 크기 및 텍스트",
    "settings_screen_timeout": "디스플레이 > 화면 자동 꺼짐", "clock_alarm_list": "시계 > 알람 목록",
    "clock_alarm_edit": "시계 > 알람 편집", "maps_home": "지도", "sms_list": "메시지 목록",
    "gmail_inbox": "Gmail 받은편지함",
}


def _fake_next(payload, timeout):
    """가짜 모드: WAIT가 아닌 첫 후보를 고른다."""
    q = payload["request"]["questions"]["decision"]
    if "SET_FONT" in q["criteria"]:
        return _fake_transport(payload, timeout)
    ids = list(q["criteria"])
    pick = next(i for i in ids if i != "WAIT")
    rest = 0.1 / (len(ids) - 1)
    return {"answers": {"decision": {"type": "choice", "choice": pick,
                                     "probabilities": {i: 0.9 if i == pick else rest for i in ids}}}}


def screen_of(kind: str, revision: int, evidence: tuple[str, ...] = ()) -> ScreenState:
    targets = tuple(t for t, p in TARGETS.items() if p.screen == kind)
    return ScreenState(projection_epoch="sim", screen_revision=revision, screen_kind=kind,
                       target_ids=targets, evidence_codes=evidence, observation_health="STABLE")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="자녀 발화")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--deadline-ms", type=int, default=5000)
    ap.add_argument("--start", default="home", help="시작 화면 종류(기본 home)")
    ap.add_argument("--max-steps", type=int, default=8)
    args = ap.parse_args(argv)

    model = (InferZoroChoice(model_revision="fixture", transport=_fake_next) if args.fake
             else InferZoroChoice.from_environment())
    if not args.fake and (not model.endpoint or not model.model_revision):
        ap.error("ZORO_DECIDE_URL과 ZORO_MODEL_REVISION을 설정하거나 --fake로 실행하세요")

    def runner(req):
        return model.run(replace(req, deadline_ms=args.deadline_ms))

    src = "FIXTURE" if args.fake else "LIVE"
    ex = ExecutionContext(trace_id=f"nav-{uuid4().hex[:8]}", source=src)

    # 1) 목표 판단
    speech = FinalUtterance(utterance_id="u1", utterance_seq=1, speaker_role="CHILD",
                            text=args.text, call_id="sim-call", source=src)
    turn = InterpretChildTurn(infer_zoro_choice=runner).run(InterpretChildTurnInput(
        mode="CALL_CHILD_LED", speech=speech, existing_goal=None, goal_catalog_revision=POLICY_REVISION,
        basis=Basis(call_id="sim-call", utterance_seq=1, mode_revision=1), execution=ex))
    d = turn.data
    if not d or not d.goal_draft:
        err = turn.diagnostics.terminal_error
        print(f"[목표] 채택된 목표 없음 (status={turn.status}, outcome={turn.outcome}"
              + (f", 오류={err.code}" if err else "") + ")")
        if d and d.goal_belief:
            top = max(d.goal_belief.probabilities.items(), key=lambda kv: kv[1])
            print(f"       1위: {top[0]} {top[1]:.3f}")
        return 1
    goal = Goal(goal_id=d.goal_draft.goal_id, goal_revision=1,
                approved_slots=d.goal_draft.approved_slots, source_utterance_id="u1")
    print(f"[목표] {goal.goal_id} ({GOAL_LABELS[goal.goal_id]}) 확률 {d.confidence:.3f} 슬롯 {goal.approved_slots}")
    if not required_arrival_evidence(goal):
        print("  이 목표는 도착 판정에 필요한 슬롯이 부족해 안내 후보가 만들어지지 않습니다(코드 규칙).")
        return 1
    print(f"[경로] 코드에 정의된 목표 경로: {' → '.join(TARGETS[t].label for t in GOAL_ROUTES[goal.goal_id])}\n")

    # 2) 화면 시뮬레이션
    enum, choose = EnumerateActionCandidates(), ChoosePostCallAction(infer_zoro_choice=runner)
    trail = NavigationTrail(entries=(), directives=(), gap_after_seq=None)
    kind, rev, evidence = args.start, 1, ()
    route = GOAL_ROUTES[goal.goal_id]
    for step in range(1, args.max_steps + 1):
        screen = screen_of(kind, rev, evidence)
        basis = Basis(help_id="sim-help", mode_revision=2, goal_revision=1,
                      projection_epoch="sim", screen_revision=rev)
        print(f"[{step}] 화면: {SCREEN_NAMES.get(kind, kind)}  (보이는 대상 {len(screen.target_ids)}개)")
        if arrival_evidence(goal, screen):
            print("    ✅ 도착 근거 확인 — 목표 완료로 판정")
            return 0
        cands = enum.run(EnumerateActionCandidatesInput(
            mode="POST_CALL_AGENT", screen=screen, instruction=None, goal=goal, trail=trail,
            policy_revision=POLICY_REVISION, basis=basis, execution=ex))
        cs = cands.data
        if not cs or not cs.candidates:
            print(f"    후보 없음 (N-16 outcome={cands.outcome}) — 이 화면에서는 안내할 버튼이 없습니다")
            return 1
        print("    [코드 후보] " + ", ".join(f"{c.choice_id}(거리 {c.path_distance})" for c in cs.candidates))
        res = choose.run(ChoosePostCallActionInput(
            mode="POST_CALL_AGENT", candidate_set=cs, goal=goal, basis=basis,
            threshold_profile="p0", execution=ex))
        out = res.data
        if res.status == "error" or out is None:
            err = res.diagnostics.terminal_error
            print(f"    오류: {err.code if err else res.status}")
            return 1
        if out.proposal is None:
            print(f"    [Zoro-1] 안내 보류 (이유: {out.reason}, 선택: {out.selected_id})")
            return 1
        p = out.proposal
        label = TARGETS[p.target_id].label if p.target_id else "뒤로"
        print(f"    [Zoro-1] 다음 행동: {p.action} '{label}'  확신 {p.confidence:.3f}")

        # 3) 사용자가 안내대로 눌렀다고 가정하고 다음 화면으로
        prof = TARGETS[p.target_id]
        rev += 1
        if p.action == "INPUT_HINT" and p.target_id == "clock.time_picker":
            evidence = (f"alarm_edit:{alarm_token(goal.approved_slots)}",)
            print("    → (시뮬레이션) 알람 시각을 입력함")
        elif p.target_id == route[-1]:
            kind = prof.destination or kind
            evidence = required_arrival_evidence(goal)
            print(f"    → (시뮬레이션) 마지막 단계 실행, 결과 근거 {evidence}")
        else:
            kind, evidence = prof.destination or kind, ()
            print(f"    → (시뮬레이션) '{label}' 누름")
    print("최대 단계 수에 도달했습니다.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
