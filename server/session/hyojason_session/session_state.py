"""N-11 저장소·N-22 장부·N-23 종료 기록을 묶어 N-13이 읽는 HelpStateReader로 내놓는다."""

from __future__ import annotations

from .common.enums import ArrivalState
from .common.types import HelpSnapshot
from .completion import CompletionLedger
from .ports import HelpStateReader, HelpTombstone
from .purge import TombstoneRegistry


class SessionStateReader:
    def __init__(
        self,
        help_store: HelpStateReader,  # N-11 (현재 mocks.InMemoryHelpStore)
        ledger: CompletionLedger,  # N-22
        tombstones: TombstoneRegistry,  # N-23
    ) -> None:
        self._help = help_store
        self._ledger = ledger
        self._tombstones = tombstones

    def latest_help_id(self, pair_id: str) -> str | None:
        return self._help.latest_help_id(pair_id)

    def get_snapshot(self, help_id: str) -> HelpSnapshot | None:
        if self._tombstones.get(help_id) is not None:
            return None  # 종료 기록이 있으면 남은 snapshot이 있어도 쓰지 않는다
        return self._help.get_snapshot(help_id)

    def get_tombstone(self, help_id: str) -> HelpTombstone | None:
        return self._tombstones.get(help_id)

    def arrival_state(self, help_id: str) -> ArrivalState:
        snapshot = self.get_snapshot(help_id)
        return self._ledger.arrival_state_for(snapshot) if snapshot else ArrivalState.NONE

    def observation_paused(self, help_id: str) -> bool:
        return self._help.observation_paused(help_id)
