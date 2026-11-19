"""N-13이 읽는 다른 노드의 경계. 실제 구현 전에는 hyojason.mocks의 Mock을 꽂는다.

| 인터페이스 | 실제 소유 노드 | 현재 구현 |
|---|---|---|
| PairDirectory | N-01 resolve_pair (B) | mocks.StaticPairDirectory |
| HelpStateReader.latest_help_id / get_snapshot / observation_paused | N-11 reduce_help_state (D) | control.SessionControl |
| HelpStateReader.get_tombstone | N-23 purge_help_and_restart (D) | mocks.InMemoryHelpStore |
| HelpStateReader.arrival_state | N-22 resolve_arrival_or_completion (D) | mocks.InMemoryHelpStore |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .common.enums import ArrivalState, EndReason, Role
from .common.types import HelpSnapshot


class PairDirectory(Protocol):
    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        """승인된 페어에서 user의 역할. 페어 밖이면 None."""


@dataclass(frozen=True)
class HelpTombstone:
    """N-23이 도움 데이터를 지운 뒤 남기는 최소 상태 (식별자와 종료 사유)."""

    pair_id: str
    help_id: str
    end_reason: EndReason
    final_event_seq: int


class HelpStateReader(Protocol):
    def latest_help_id(self, pair_id: str) -> str | None:
        """이 페어에서 서버가 최신으로 보는 도움(활성 또는 막 끝난)."""

    def get_snapshot(self, help_id: str) -> HelpSnapshot | None:
        """지워지지 않은 도움의 권위 snapshot. 모르는 도움이면 None."""

    def get_tombstone(self, help_id: str) -> HelpTombstone | None:
        """끝나서 지워진 도움의 기록. 없으면 None."""

    def arrival_state(self, help_id: str) -> ArrivalState:
        """N-22가 자녀에게 낸 도착 추정 알림 상태."""

    def observation_paused(self, help_id: str) -> bool:
        """관찰 권한이 중단됐거나 아직 승인되지 않았는지 확인한다."""
