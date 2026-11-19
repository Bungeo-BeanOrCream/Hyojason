"""공통 열거형. 값은 docs/architecture.md·contracts.md의 상태 이름을 그대로 쓴다."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    PARENT = "PARENT"
    CHILD = "CHILD"


class CallState(StrEnum):
    IDLE = "IDLE"
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    RECONNECTING = "RECONNECTING"
    ENDED = "ENDED"


class HelpState(StrEnum):
    LISTENING = "LISTENING"
    ACTIVE = "ACTIVE"
    ARRIVAL_PENDING = "ARRIVAL_PENDING"
    ENDED = "ENDED"


class Mode(StrEnum):
    CALL_CHILD_LED = "CALL_CHILD_LED"
    POST_CALL_AGENT = "POST_CALL_AGENT"


class ObservationHealth(StrEnum):
    """부모 화면 관찰 축 (architecture.md 상태 전이 표)."""

    NOT_GRANTED = "NOT_GRANTED"
    ACTIVE = "ACTIVE"
    GAP = "GAP"
    STOPPED = "STOPPED"


class Action(StrEnum):
    TAP = "TAP"
    BACK = "BACK"
    SCROLL_DOWN = "SCROLL_DOWN"
    SCROLL_UP = "SCROLL_UP"
    INPUT_HINT = "INPUT_HINT"
    WAIT = "WAIT"


class ArrivalState(StrEnum):
    """N-22가 관리하는 도착 추정 알림 상태 (arrival.suspected / revoked)."""

    NONE = "NONE"
    SUSPECTED = "SUSPECTED"
    REVOKED = "REVOKED"


class EndReason(StrEnum):
    """도움 종료 사유. N-23의 help.ended(reason)과 같은 값."""

    CHILD_COMPLETED = "CHILD_COMPLETED"
    PARENT_STOPPED = "PARENT_STOPPED"
    NO_GOAL_AT_HANGUP = "NO_GOAL_AT_HANGUP"


class Source(StrEnum):
    LIVE = "LIVE"
    FIXTURE = "FIXTURE"
