"""MOCK of WS /v1/events 제어 채널 (D, 아직 미구현). 보낸 이벤트를 기록하고 연결 상태를 흉내 낸다."""

from __future__ import annotations

from ..common.enums import Role
from ..common.events import ControlEvent


class RecordingEventSink:
    def __init__(self, *, online: dict[Role, bool] | None = None) -> None:
        self.online = {Role.PARENT: True, Role.CHILD: True, **(online or {})}
        self.sent: list[tuple[Role, ControlEvent]] = []

    def send(self, role: Role, event: ControlEvent) -> bool:
        if not self.online.get(role, False):
            return False
        self.sent.append((role, event))
        return True

    def events_of(self, role: Role, type_: str) -> list[ControlEvent]:
        return [e for r, e in self.sent if r is role and e.type == type_]
