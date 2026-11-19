"""서버 상태 자료형 (docs/contracts.md '자료형' 표).

HelpSnapshot은 N-11(D)의 원본 상태다. 계약 표의 필드에 더해 아래 세 필드를 둔다.
- pair_id: 다른 페어의 snapshot을 거절하기 위해 (N-21 21.3, N-13 13.3)
- call_id: 역할 앱이 제어 이벤트 봉투(v, type, event_id, pair_id, call_id, help_id)를 채우기 위해
- end_reason: 부모 완료 문구를 자녀 완료일 때만 내기 위해 (N-20, N-23)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .enums import (
    Action,
    CallState,
    EndReason,
    HelpState,
    Mode,
    ObservationHealth,
    Role,
)


@dataclass(frozen=True)
class ActorContext:
    """인증 토큰에서 얻은 호출자. 클라이언트가 보낸 역할 값을 믿지 않고 이것과 대조한다."""

    user_id: str
    pair_id: str
    role: Role


@dataclass(frozen=True)
class Goal:
    goal_id: str
    goal_revision: int
    approved_slots: Mapping[str, str]
    source_utterance_id: str


@dataclass(frozen=True)
class Instruction:
    instruction_id: str
    instruction_revision: int
    source_utterance_id: str
    target_key: str | None
    action: Action
    origin_screen_revision: int | None


@dataclass(frozen=True)
class ScreenState:
    projection_epoch: str
    screen_revision: int
    screen_kind: str
    target_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    observation_health: ObservationHealth


@dataclass(frozen=True)
class HelpSnapshot:
    pair_id: str
    call_id: str | None
    help_id: str
    call_state: CallState
    help_state: HelpState
    mode: Mode
    mode_revision: int
    goal: Goal | None
    instruction: Instruction | None
    screen: ScreenState | None
    trail_cursor: int
    consent_revision: int
    event_seq: int
    end_reason: EndReason | None


def _is_uint(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_id(value: object) -> bool:
    return isinstance(value, str) and value != ""


def snapshot_problems(snapshot: object) -> list[str]:
    """HelpSnapshot 구조 검사. 문제 필드 이름 목록을 돌려준다(빈 목록이면 유효)."""

    if not isinstance(snapshot, HelpSnapshot):
        return ["snapshot"]
    problems: list[str] = []
    if not _is_id(snapshot.pair_id):
        problems.append("pair_id")
    if snapshot.call_id is not None and not _is_id(snapshot.call_id):
        problems.append("call_id")
    if not _is_id(snapshot.help_id):
        problems.append("help_id")
    for name, enum_type in (
        ("call_state", CallState),
        ("help_state", HelpState),
        ("mode", Mode),
    ):
        if not isinstance(getattr(snapshot, name), enum_type):
            problems.append(name)
    for name in ("mode_revision", "trail_cursor", "consent_revision", "event_seq"):
        if not _is_uint(getattr(snapshot, name)):
            problems.append(name)
    if snapshot.end_reason is not None and not isinstance(snapshot.end_reason, EndReason):
        problems.append("end_reason")
    if snapshot.help_state is HelpState.ENDED and snapshot.end_reason is None:
        problems.append("end_reason")
    if snapshot.goal is not None and (
        not isinstance(snapshot.goal, Goal) or not _is_uint(snapshot.goal.goal_revision)
    ):
        problems.append("goal")
    if snapshot.instruction is not None and (
        not isinstance(snapshot.instruction, Instruction)
        or not _is_uint(snapshot.instruction.instruction_revision)
        or not isinstance(snapshot.instruction.action, Action)
    ):
        problems.append("instruction")
    if snapshot.screen is not None and (
        not isinstance(snapshot.screen, ScreenState)
        or not _is_uint(snapshot.screen.screen_revision)
        or not isinstance(snapshot.screen.observation_health, ObservationHealth)
    ):
        problems.append("screen")
    return problems
