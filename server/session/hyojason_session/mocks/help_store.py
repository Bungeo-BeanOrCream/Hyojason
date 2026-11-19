"""MOCK of N-11 상태 저장 — 격리 테스트용.

실제 N-11은 control.SessionControl(help_state.HelpSession)이다. 이 Mock은 N-13·N-23을 N-11 없이
단독으로 시험할 때 쓴다. 읽기(HelpStateReader)와 purge(HelpScopedStore, scope=HELP_STATE)를 제공하고,
N-13 단독 테스트를 위해 도착 상태(set_arrival_state)와 종료 기록(end_help)도 흉내 낸다.
실제 N-22·N-23과 묶을 때는 session_state.SessionStateReader를 쓴다.
"""

from __future__ import annotations

import dataclasses

from ..common.enums import ArrivalState, CleanupScope, EndReason, HelpState, ObservationHealth
from ..common.types import HelpSnapshot
from ..ports import HelpTombstone


class InMemoryHelpStore:
    scope = CleanupScope.HELP_STATE

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

    # --- HelpScopedStore -------------------------------------------------
    def purge(self, help_id: str) -> None:
        """도움 내용만 지운다. 페어의 최신 도움 포인터는 남겨 N-13이 ENDED로 판단하게 한다."""
        self._snapshots.pop(help_id, None)
        self._arrival.pop(help_id, None)

    # --- 테스트·시연용 쓰기 (N-11 대체) -------------------------------------
    def put_snapshot(self, snapshot: HelpSnapshot) -> None:
        """N-11이 이벤트를 적용해 snapshot을 커밋한 것처럼 저장한다."""
        self._snapshots[snapshot.help_id] = snapshot
        self._latest[snapshot.pair_id] = snapshot.help_id

    def set_arrival_state(self, help_id: str, state: ArrivalState) -> None:
        self._arrival[help_id] = state

    def end_help(self, help_id: str, reason: EndReason, *, purge: bool = True) -> None:
        """N-13 단독 테스트용 종료. purge=True면 내용을 지우고 종료 기록만 남긴다."""
        snap = self._snapshots[help_id]
        final_seq = snap.event_seq + 1
        if purge:
            self.purge(help_id)
        else:
            self._snapshots[help_id] = dataclasses.replace(
                snap, help_state=HelpState.ENDED, end_reason=reason, event_seq=final_seq
            )
        self._tombstones[help_id] = HelpTombstone(
            pair_id=snap.pair_id,
            help_id=help_id,
            end_reason=reason,
            final_event_seq=final_seq,
            end_event_id=f"end-{help_id}",
            resume_requires_consent=reason is EndReason.PARENT_STOPPED,
        )

    def restart(self, *, keep_help_ids: tuple[str, ...] = ()) -> InMemoryHelpStore:
        """서버 재시작을 흉내 낸다. keep_help_ids만 영속 저장소에서 복구된 것으로 본다."""
        fresh = InMemoryHelpStore()
        for help_id in keep_help_ids:
            fresh.put_snapshot(self._snapshots[help_id])
        return fresh
