"""MOCK of N-01 resolve_pair (B 담당). 고정된 페어 표에서 역할을 찾는다."""

from __future__ import annotations

from collections.abc import Mapping

from ..common.enums import Role


class StaticPairDirectory:
    def __init__(self, pairs: Mapping[str, Mapping[str, Role]]) -> None:
        # {pair_id: {user_id: Role}}
        self._pairs = {pid: dict(members) for pid, members in pairs.items()}

    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        return self._pairs.get(pair_id, {}).get(user_id)
