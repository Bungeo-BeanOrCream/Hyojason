"""N-23 클라이언트 쪽 참조 구현 — 서버에서 실행하지 않는다.

A(부모 앱 B-06)·B(자녀 앱)가 Kotlin으로 옮길 규칙을 실행 가능한 명세로 둔다.
tests/test_n23_purge_help_and_restart.py의 클라이언트 케이스를 JVM 단위 테스트로 가져가면 된다.

규칙 (N-23 처리 규칙, N-20). '지원 대상'은 이번 도움의 assisted_role 기기(#6, 기본 부모)다.
1. 지원 대상의 '안내 끄기'는 네트워크 응답을 기다리지 않고 로컬에서 먼저 모두 지운 뒤 help.stop을 보낸다.
2. help.ended를 받으면 지원 대상은 캡처·좌표·타이머·테두리·상단 띠를, 상대는 완료 버튼·도착 알림을 지운다.
3. 지원 대상의 "다 됐어요!"는 상대가 완료했을 때(부모 지원이면 CHILD_COMPLETED) end_event_id마다 한 번만.
4. 지금 도움과 다른 help_id의 help.ended는 무시한다 (새 도움을 늦은 종료로 지우지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .common.enums import Role
from .common.events import ControlEvent
from .common.roles import completed_reason, counterpart


class LocalAction(StrEnum):
    STOP_CAPTURE = "STOP_CAPTURE"
    CLEAR_COORDINATES = "CLEAR_COORDINATES"
    CANCEL_TIMERS = "CANCEL_TIMERS"
    CLEAR_OVERLAY = "CLEAR_OVERLAY"
    CLEAR_BANNER = "CLEAR_BANNER"
    SEND_STOP = "SEND_STOP"
    SHOW_COMPLETION = "SHOW_COMPLETION"
    CLEAR_COMPLETE_BUTTON = "CLEAR_COMPLETE_BUTTON"
    CLEAR_ARRIVAL_NOTICE = "CLEAR_ARRIVAL_NOTICE"


ASSISTED_CLEAR = (
    LocalAction.STOP_CAPTURE,
    LocalAction.CLEAR_COORDINATES,
    LocalAction.CANCEL_TIMERS,
    LocalAction.CLEAR_OVERLAY,
    LocalAction.CLEAR_BANNER,
)
COUNTERPART_CLEAR = (LocalAction.CLEAR_COMPLETE_BUTTON, LocalAction.CLEAR_ARRIVAL_NOTICE)


@dataclass
class ClientHelpCleanup:
    role: Role
    assisted_role: Role = Role.PARENT
    help_id: str | None = None
    active: bool = False
    completions_shown: set[str] = field(default_factory=set)

    def start_help(self, help_id: str) -> None:
        self.help_id, self.active = help_id, True

    def _clear(self) -> list[LocalAction]:
        self.active = False
        return list(ASSISTED_CLEAR if self.role is self.assisted_role else COUNTERPART_CLEAR)

    def parent_stop_pressed(self) -> list[LocalAction]:
        """규칙 1: 로컬 정리가 먼저, 서버 전송은 그다음 (오프라인이어도 화면은 이미 깨끗하다)."""
        if self.role is not self.assisted_role or not self.active:
            return []
        return self._clear() + [LocalAction.SEND_STOP]

    def on_help_ended(self, event: ControlEvent) -> list[LocalAction]:
        if event.type != "help.ended" or event.help_id != self.help_id:
            return []  # 규칙 4
        actions = self._clear()
        end_event_id = event.payload.get("end_event_id", event.event_id)
        if (
            self.role is self.assisted_role
            and event.payload.get("reason") == completed_reason(counterpart(self.assisted_role)).value
            and end_event_id not in self.completions_shown
        ):
            self.completions_shown.add(end_event_id)
            actions.append(LocalAction.SHOW_COMPLETION)  # 규칙 3
        return actions
