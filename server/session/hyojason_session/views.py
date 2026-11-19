"""역할별 화면 투영 타입 (N-21이 만들고 N-13 재접속 응답이 그대로 싣는다).

contracts.md에는 ParentView/ChildView/RoleSnapshot의 필드 정의가 없어 여기서 정한다.
팀 합의 전 초안이며 docs/work/D-role-view-contract.md에 같은 표가 있다.

- ChildView는 N-21 규칙대로 call_state, can_complete, arrival_indicator 세 키만 갖는다.
- ParentView는 모드·검증된 현재 목표·허용 지시 상태·안내 끄기·완료 문구만 갖는다.
  화면(ScreenState)·target_ids·좌표·GuideProposal·발화 원문/ID·이력은 넣지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .common.enums import Action, CallState, HelpState, Mode, Role

# 부모 완료 문구 "다 됐어요!"의 메시지 키. 자녀 완료로 끝났을 때만 채운다 (N-20).
COMPLETION_MESSAGE_KEY = "help.completed"


class ParentStatus(StrEnum):
    """부모 상단 띠(N-20)가 고를 상태 문구."""

    LISTENING = "LISTENING"  # 통화 중, 아직 지시·목표 없음 (듣는 중)
    CALL_GUIDING = "CALL_GUIDING"  # 통화 중 자녀 주도 안내
    POST_CALL_GUIDING = "POST_CALL_GUIDING"  # 통화 후 마지막 목표 이어서 안내
    ARRIVAL_PENDING = "ARRIVAL_PENDING"  # 통화 후 도착 추정, 자녀 확인 대기
    COMPLETED = "COMPLETED"  # 자녀가 도움 완료
    STOPPED = "STOPPED"  # 부모가 안내 끄기
    ENDED = "ENDED"  # 그 밖의 종료 (예: 통화 종료 시 목표 없음)


class ArrivalIndicator(StrEnum):
    NONE = "NONE"
    SUSPECTED = "SUSPECTED"


@dataclass(frozen=True)
class ParentGoalView:
    goal_id: str
    goal_revision: int
    approved_slots: Mapping[str, str]


@dataclass(frozen=True)
class ParentInstructionView:
    """통화 중 현재 지시의 허용 상태. 원문·source_utterance_id·좌표는 없다."""

    instruction_id: str
    instruction_revision: int
    action: Action
    target_key: str | None


@dataclass(frozen=True)
class ParentView:
    status: ParentStatus
    call_state: CallState
    help_state: HelpState
    mode: Mode
    mode_revision: int
    goal: ParentGoalView | None
    instruction: ParentInstructionView | None
    can_stop_guidance: bool
    completion_message_key: str | None


@dataclass(frozen=True)
class ChildView:
    call_state: CallState
    can_complete: bool
    arrival_indicator: ArrivalIndicator


@dataclass(frozen=True)
class RoleSnapshot:
    """재접속 응답에 싣는 역할 투영. 식별자는 제어 이벤트 봉투를 채우는 데만 쓴다."""

    role: Role
    pair_id: str
    call_id: str | None
    help_id: str
    parent_view: ParentView | None
    child_view: ChildView | None
