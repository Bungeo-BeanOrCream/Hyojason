"""B-01 세션 인증 (#10 합의안): 서버가 invite/claim 때 무작위 opaque 토큰을 발급하고,
앱은 이후 REST(와 D의 WS)에 `Authorization: Bearer <token>`으로 보낸다.

토큰 → actor_id만 기억한다. 페어·역할은 PairRegistry에서 매번 찾는다(다시 연결해도 어긋나지 않게).
토큰 원문은 저장하지 않고 SHA-256만 키로 둔다.
ponytail: 메모리 저장, 만료 없음. 서버를 재시작하면 앱은 가족 연결부터 다시 한다. 영속이 필요하면 DB로.
"""

from __future__ import annotations

import hashlib
import secrets

from hyojason_session.common.result import new_id

BEARER = "Bearer "


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Tokens:
    def __init__(self) -> None:
        self._actors: dict[str, str] = {}  # sha256(token) -> actor_id

    def issue(self) -> tuple[str, str]:
        """새 기기 하나 = 새 actor. (actor_id, token)"""
        actor_id, token = new_id("actor"), secrets.token_urlsafe(32)
        self._actors[_key(token)] = actor_id
        return actor_id, token

    def actor_of(self, authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith(BEARER):
            return None
        return self._actors.get(_key(authorization[len(BEARER):]))
