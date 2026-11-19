"""D 노드가 다른 노드와 만나는 경계. 실제 구현 전에는 hyojason_session.mocks의 Mock을 꽂는다.

| 인터페이스 | 실제 소유 노드 | 현재 구현 |
|---|---|---|
| PairDirectory | N-01 resolve_pair (B) | mocks.StaticPairDirectory (MOCK) |
| HelpStateReader.latest_help_id / get_snapshot / observation_paused | N-11 reduce_help_state (D) | control.SessionControl |
| HelpStateReader.get_tombstone | N-23 purge_help_and_restart (D) | purge.TombstoneRegistry |
| HelpStateReader.arrival_state | N-22 resolve_arrival_or_completion (D) | control.SessionControl (N-22가 승인한 도착을 N-11이 반영한 상태) |
| HelpScopedStore (HELP_STATE) | N-11 (D) | control._HelpStatePurge (격리 테스트: mocks.InMemoryHelpStore) |
| HelpScopedStore (NAVIGATION_TRAIL) | N-12 (D) | control._TrailPurge (격리 테스트: mocks.ScopedBuffer) |
| HelpScopedStore (INFERENCE_RESULTS) | B-05 (C) | 아직 서버에 데이터 없음: control._NoDataHeld (테스트: mocks.ScopedBuffer) |
| HelpScopedStore (PENDING_UTTERANCES) | B-02 (B) | 아직 서버에 데이터 없음: control._NoDataHeld (테스트: mocks.ScopedBuffer) |
| HelpScopedStore (ARRIVAL_NOTICE) | N-22 (D) | completion.CompletionLedger |
| EventSink | WS /v1/events 제어 채널 (D) | mocks.RecordingEventSink (MOCK). 미연결이면 control._NoChannel |

실제 서버 경로는 control.SessionControl이 N-11·N-12·N-22·N-23을 묶는다.
session_state.SessionStateReader는 N-11 없이 N-22·N-23을 시험하는 격리 배선이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .common.enums import ArrivalState, CleanupScope, EndReason, Role
from .common.events import ControlEvent
from .common.types import HelpSnapshot


class PairDirectory(Protocol):
    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        """승인된 페어에서 user의 역할. 페어 밖이면 None."""


@dataclass(frozen=True)
class HelpTombstone:
    """도움 종료 뒤 남기는 최소 상태. 사용자 내용 없이 식별자와 종료 사유만.

    앞 네 필드는 N-11(control.SessionControl)도 만든다. 뒤 두 필드는 N-23이 채운다.
    """

    pair_id: str
    help_id: str
    end_reason: EndReason
    final_event_seq: int
    end_event_id: str | None = None
    # 다음 도움을 열기 전에 부모의 재활성 동의가 필요한가 (부모 중단·동의 철회)
    resume_requires_consent: bool = False


class HelpStateReader(Protocol):
    def latest_help_id(self, pair_id: str) -> str | None:
        """이 페어에서 서버가 최신으로 보는 도움(활성 또는 막 끝난)."""

    def get_snapshot(self, help_id: str) -> HelpSnapshot | None:
        """지워지지 않은 도움의 권위 snapshot. 모르는 도움이면 None."""

    def get_tombstone(self, help_id: str) -> HelpTombstone | None:
        """끝나서 지워진 도움의 기록. 없으면 None."""

    def arrival_state(self, help_id: str) -> ArrivalState:
        """N-22가 자녀에게 낸 도착 추정 알림 상태 (현재 goal/mode revision 기준)."""

    def observation_paused(self, help_id: str) -> bool:
        """관찰 권한이 중단됐거나 아직 승인되지 않았는지 확인한다."""


class HelpScopedStore(Protocol):
    """도움 단위로 데이터를 들고 있는 저장소. N-23이 종료 때 purge를 부른다(멱등)."""

    scope: CleanupScope

    def purge(self, help_id: str) -> None: ...


class EventSink(Protocol):
    def send(self, role: Role, event: ControlEvent) -> bool:
        """역할의 제어 채널에 이벤트를 넘긴다. 연결이 없어 못 넘기면 False."""
