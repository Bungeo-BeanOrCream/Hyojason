"""N-13 클라이언트 쪽 참조 구현 — 서버에서 실행하지 않는다.

N-13은 '서버/클라이언트' 노드다. 서버가 보낸 ReconnectData를 Android 앱이 어떻게 적용해야
하는지를 실행 가능한 명세로 둔다. A(부모 Overlay)·B(자녀 UI)가 Kotlin으로 옮길 때
tests/test_n13_reconcile_reconnect.py의 클라이언트 케이스를 그대로 단위 테스트로 쓰면 된다.

규칙 (N-13 처리 규칙·구현 완료 기준)
1. 재접속 응답을 받으면 부모는 먼저 직전 Overlay와 3초 타이머를 지운다.
2. snapshot이 없으면(ENDED/UNKNOWN/NO_HELP) 로컬 안내·도움 캐시를 지운다.
3. must_reobserve면 새 화면 관찰을 보내기 전까지 어떤 안내도 표시하지 않는다.
4. 재접속 시점 이전에 만들어진 안내, 그리고 최신 goal/mode/instruction revision과
   맞지 않는 늦은 안내는 무시한다. 관찰·좌표가 새로 확인된 뒤의 안내만 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .common.enums import Role
from .common.result import Basis
from .reconnect import ReconnectData


class ClientAction(StrEnum):
    CLEAR_OVERLAY = "CLEAR_OVERLAY"
    CANCEL_TIMERS = "CANCEL_TIMERS"
    CLEAR_HELP_CACHE = "CLEAR_HELP_CACHE"
    REQUEST_REOBSERVE = "REQUEST_REOBSERVE"
    REPLACE_VIEW = "REPLACE_VIEW"


@dataclass
class ClientResumeGate:
    role: Role
    help_id: str | None = None
    goal_revision: int | None = None
    mode_revision: int | None = None
    instruction_revision: int | None = None
    resume_event_seq: int = 0
    awaiting_reobserve: bool = False
    reobserved_screen_revision: int | None = None

    def apply_reconnect(self, data: ReconnectData) -> list[ClientAction]:
        out: list[ClientAction] = []
        if self.role is Role.PARENT:
            out += [ClientAction.CLEAR_OVERLAY, ClientAction.CANCEL_TIMERS]
        self.resume_event_seq = data.server_event_seq
        self.reobserved_screen_revision = None

        snap = data.snapshot
        if snap is None:
            out.append(ClientAction.CLEAR_HELP_CACHE)
            self.help_id = None
            self.goal_revision = self.mode_revision = self.instruction_revision = None
            self.awaiting_reobserve = False
        else:
            if self.help_id is not None and self.help_id != snap.help_id:
                out.append(ClientAction.CLEAR_HELP_CACHE)
            self.help_id = snap.help_id
            pv = snap.parent_view
            self.goal_revision = pv.goal.goal_revision if pv and pv.goal else None
            self.mode_revision = pv.mode_revision if pv else None
            self.instruction_revision = (
                pv.instruction.instruction_revision if pv and pv.instruction else None
            )
            out.append(ClientAction.REPLACE_VIEW)
            self.awaiting_reobserve = data.must_reobserve
            if data.must_reobserve:
                out.append(ClientAction.REQUEST_REOBSERVE)
        return out

    def on_screen_observed(self, screen_revision: int) -> None:
        """재접속 뒤 첫 안정 화면을 서버에 보낸 시점."""
        self.awaiting_reobserve = False
        self.reobserved_screen_revision = screen_revision

    def can_display(self, guide_basis: Basis) -> bool:
        """서버 안내(GuideProposal 등)의 Basis를 보고 표시해도 되는지 판단한다."""
        if self.role is not Role.PARENT or self.help_id is None:
            return False
        if self.awaiting_reobserve or self.reobserved_screen_revision is None:
            return False
        b = guide_basis
        if b.help_id != self.help_id or b.event_seq is None:
            return False
        if b.event_seq <= self.resume_event_seq:  # 재접속 전에 만든 안내는 재사용하지 않는다
            return False
        if b.mode_revision != self.mode_revision:
            return False
        if self.goal_revision is not None and b.goal_revision != self.goal_revision:
            return False
        if self.instruction_revision is not None and (
            b.instruction_revision != self.instruction_revision
        ):
            return False
        return b.screen_revision is not None and b.screen_revision >= self.reobserved_screen_revision
