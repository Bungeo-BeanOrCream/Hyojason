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
    """도움 종료 사유. N-23의 help.ended(reason)과 같은 값.

    완료는 지원 대상의 상대가, 안내 끄기는 지원 대상이 한다(#6 제안). 값 이름은 실제로 누른 가족
    역할이다: 부모 지원이면 CHILD_COMPLETED / PARENT_STOPPED, 자녀 지원이면 PARENT_COMPLETED / CHILD_STOPPED.
    """

    CHILD_COMPLETED = "CHILD_COMPLETED"
    PARENT_STOPPED = "PARENT_STOPPED"
    PARENT_COMPLETED = "PARENT_COMPLETED"
    CHILD_STOPPED = "CHILD_STOPPED"
    NO_GOAL_AT_HANGUP = "NO_GOAL_AT_HANGUP"
    CONSENT_REVOKED = "CONSENT_REVOKED"


class Source(StrEnum):
    LIVE = "LIVE"
    FIXTURE = "FIXTURE"


class ActorRole(StrEnum):
    """제어 명령·판단을 보낸 쪽. AGENT는 서버 내부(B-05 판단, 통화 종료 처리)다."""

    PARENT = "PARENT"
    CHILD = "CHILD"
    AGENT = "AGENT"


class HelpCommand(StrEnum):
    """help.complete(자녀) / help.stop(부모)."""

    COMPLETE = "COMPLETE"
    STOP = "STOP"


class CleanupScope(StrEnum):
    """N-23이 도움 종료 때 비우는 범위."""

    # 서버
    HELP_STATE = "HELP_STATE"  # 목표·지시·모드 등 도움 상태 (N-11)
    NAVIGATION_TRAIL = "NAVIGATION_TRAIL"  # 전이·지시 근거 이력 (N-12)
    INFERENCE_RESULTS = "INFERENCE_RESULTS"  # 추론 후보·제안·판단 결과 (B-05)
    PENDING_UTTERANCES = "PENDING_UTTERANCES"  # 대기 중인 확정 발화 (B-02)
    ARRIVAL_NOTICE = "ARRIVAL_NOTICE"  # 도착 추정 알림 상태 (N-22)
    # 단말 (help.ended 전달로 정리)
    PARENT_DEVICE = "PARENT_DEVICE"  # 캡처·좌표·타이머·테두리·상단 띠
    CHILD_DEVICE = "CHILD_DEVICE"  # 완료 버튼·도착 알림
