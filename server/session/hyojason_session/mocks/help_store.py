"""MOCK of N-11(상태 저장)·N-22(도착 알림 상태)·N-23(종료 기록).

N-11 reducer가 생기면 이 저장소의 쓰기 메서드(put_snapshot, end_help 등)는
N-11/N-23의 커밋 경로로 대체되고, N-13은 읽기 인터페이스(HelpStateReader)만 계속 쓴다.
"""

from __future__ import annotations

import dataclasses

from ..common.enums import ArrivalState, EndReason, HelpState, ObservationHealth
from ..common.types import HelpSnapshot
from ..ports import HelpTombstone


class InMemoryHelpStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, HelpSnapshot] = {}
        self._tombstones: dict[str, HelpTombstone] = {}
        self._latest: dict[str, str] = {}
        self._arrival: dict[str, ArrivalState] = {}

    # --- HelpStateReader -------------------------------------------------
    def latest_help_id(self, pair_id: str) -> str | None:
        return self._latest.get(pair_id)

    def get_snapshot(self, help_id: str) -> HelpSnapshot | None:
        return self._snapshots.get(help_id)

    def get_tombstone(self, help_id: str) -> HelpTombstone | None:
        return self._tombstones.get(help_id)

    def arrival_state(self, help_id: str) -> ArrivalState:
        return self._arrival.get(help_id, ArrivalState.NONE)

    def observation_paused(self, help_id: str) -> bool:
        snapshot = self._snapshots.get(help_id)
        return (
            snapshot is not None and snapshot.screen is not None
            and snapshot.screen.observation_health in (ObservationHealth.STOPPED, ObservationHealth.NOT_GRANTED)
        )

    # --- 테스트·시연용 쓰기 (N-11/N-22/N-23 대체) ---------------------------
    def put_snapshot(self, snapshot: HelpSnapshot) -> None:
        """N-11이 이벤트를 적용해 snapshot을 커밋한 것처럼 저장한다."""
        self._snapshots[snapshot.help_id] = snapshot
        self._latest[snapshot.pair_id] = snapshot.help_id

    def set_arrival_state(self, help_id: str, state: ArrivalState) -> None:
        self._arrival[help_id] = state

    def end_help(self, help_id: str, reason: EndReason, *, purge: bool = True) -> None:
        """N-23처럼 도움을 끝낸다. purge=True면 내용은 지우고 종료 기록만 남긴다."""
        snap = self._snapshots[help_id]
        final_seq = snap.event_seq + 1
        if purge:
            del self._snapshots[help_id]
            self._arrival.pop(help_id, None)
        else:
            self._snapshots[help_id] = dataclasses.replace(
                snap, help_state=HelpState.ENDED, end_reason=reason, event_seq=final_seq
            )
        self._tombstones[help_id] = HelpTombstone(
            pair_id=snap.pair_id, help_id=help_id, end_reason=reason, final_event_seq=final_seq
        )

    def restart(self, *, keep_help_ids: tuple[str, ...] = ()) -> InMemoryHelpStore:
        """서버 재시작을 흉내 낸다. keep_help_ids만 영속 저장소에서 복구된 것으로 본다."""
        fresh = InMemoryHelpStore()
        for help_id in keep_help_ids:
            fresh.put_snapshot(self._snapshots[help_id])
        return fresh
