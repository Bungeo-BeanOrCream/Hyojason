"""MOCK of B-01 인증 토큰 검증 (B 담당, 이슈 #10 3번 '인증 토큰 형식' 답 전까지).

고정 토큰 → ActorContext 표로 역할·페어를 정한다. 실제 토큰 검증이 들어오면 같은 메서드
(authenticate, role_of)를 가진 구현으로 바꾼다. 공개 레포에는 가짜 토큰만 둔다(AGENTS.md).
"""

from __future__ import annotations

from collections.abc import Mapping

from ..common.enums import Role
from ..common.types import ActorContext


class MockTokenAuthenticator:
    def __init__(self, tokens: Mapping[str, ActorContext]) -> None:
        self._tokens = dict(tokens)

    def authenticate(self, token: str | None) -> ActorContext | None:
        return self._tokens.get(token) if token else None

    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        """N-13이 쓰는 PairDirectory (N-01 Mock)."""
        for actor in self._tokens.values():
            if actor.pair_id == pair_id and actor.user_id == user_id:
                return actor.role
        return None

    @classmethod
    def from_spec(cls, spec: str) -> MockTokenAuthenticator:
        """'token=user:pair:ROLE;token2=user2:pair:ROLE' 형식 (환경 변수용)."""
        tokens: dict[str, ActorContext] = {}
        for item in filter(None, (part.strip() for part in spec.split(";"))):
            token, _, rest = item.partition("=")
            user, pair, role = rest.split(":")
            tokens[token] = ActorContext(user_id=user, pair_id=pair, role=Role(role))
        if not tokens:
            raise ValueError("no mock tokens configured")
        return cls(tokens)
