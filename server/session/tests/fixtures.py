"""테스트용 FIXTURE 값.

screen_kind / target_id 값은 GitHub 이슈 #3의 A 초안(2026-11-19)을 따른 임시 값이다.
goal_id는 docs/scenarios.md의 지원 목표 ID(CLOCK_ALARM 등)다. N-13/N-21은 이 값을
불투명 문자열로만 다루므로 #3 합의 결과가 바뀌어도 로직은 바뀌지 않는다.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from hyojason_session.common.enums import (
    Action,
    CallState,
    HelpState,
    Mode,
    ObservationHealth,
    Role,
)
from hyojason_session.common.types import ActorContext, Goal, HelpSnapshot, Instruction, ScreenState

PAIR = "pair-fixture"
OTHER_PAIR = "pair-other"
PARENT_USER = "user-parent"
CHILD_USER = "user-child"
TRACE = "trace-fixture"

PARENT = ActorContext(user_id=PARENT_USER, pair_id=PAIR, role=Role.PARENT)
CHILD = ActorContext(user_id=CHILD_USER, pair_id=PAIR, role=Role.CHILD)

# 자녀 응답에 나오면 안 되는 키 (목표·지시·화면·이력·좌표·원문).
# parent_view/child_view 키 자체는 계약상 항상 있고 다른 역할 쪽은 null이다.
CHILD_FORBIDDEN_KEYS = {
    "goal", "goal_id", "goal_revision", "approved_slots", "instruction", "instruction_id",
    "instruction_revision", "target_key", "target_id", "target_ids", "screen", "screen_kind",
    "screen_revision", "projection_epoch", "evidence_codes", "trail", "trail_cursor",
    "status", "mode", "help_state", "source_utterance_id", "text", "rect",
}
# 부모 응답에도 나오면 안 되는 키 (화면 원본 요약·좌표·안내 명령·원문 참조)
PARENT_FORBIDDEN_KEYS = {
    "screen", "screen_kind", "target_ids", "evidence_codes", "projection_epoch",
    "screen_revision", "trail", "trail_cursor", "source_utterance_id", "text", "rect",
    "decision_id", "guide",
}


def goal(rev: int = 1, goal_id: str = "CLOCK_ALARM") -> Goal:
    return Goal(goal_id=goal_id, goal_revision=rev, approved_slots={"time": "06:00"},
                source_utterance_id=f"utt-goal-{rev}")


def instruction(rev: int = 1, target: str = "settings.display") -> Instruction:
    return Instruction(instruction_id=f"instr-{rev}", instruction_revision=rev,
                       source_utterance_id=f"utt-instr-{rev}", target_key=target,
                       action=Action.TAP, origin_screen_revision=5)


def screen(rev: int = 5, health: ObservationHealth = ObservationHealth.ACTIVE) -> ScreenState:
    return ScreenState(projection_epoch="epoch-1", screen_revision=rev,
                       screen_kind="settings_home", target_ids=("settings.display",),
                       evidence_codes=("label_match",), observation_health=health)


def snapshot(**overrides: Any) -> HelpSnapshot:
    base = HelpSnapshot(
        pair_id=PAIR, call_id="call-1", help_id="help-1",
        call_state=CallState.ACTIVE, help_state=HelpState.ACTIVE,
        mode=Mode.CALL_CHILD_LED, mode_revision=1,
        goal=goal(1), instruction=instruction(1), screen=screen(5),
        trail_cursor=3, consent_revision=1, event_seq=10, end_reason=None,
    )
    return dataclasses.replace(base, **overrides)


def post_call(**overrides: Any) -> HelpSnapshot:
    """확정 hangup 뒤: 같은 help_id, POST_CALL_AGENT, 통화 중 지시는 취소."""
    values = dict(call_state=CallState.ENDED, mode=Mode.POST_CALL_AGENT, mode_revision=2,
                  instruction=None, event_seq=12)
    values.update(overrides)
    return snapshot(**values)


def all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys |= all_keys(v)
    elif isinstance(value, list):
        for v in value:
            keys |= all_keys(v)
    return keys
