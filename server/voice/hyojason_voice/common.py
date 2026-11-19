"""B 노드가 같이 쓰는 작은 도우미. 계약 타입 자체는 hyojason_session(D)에서 가져온다."""

from __future__ import annotations

from typing import TypeVar

from hyojason_session.common.enums import Role
from hyojason_session.common.result import Basis, NodeResult, NodeRun, Status

T = TypeVar("T")


def partial(run: NodeRun, outcome: str, data: T, basis: Basis) -> NodeResult[T]:
    # ponytail: D의 NodeRun에 partial()이 없어 내부 _diagnostic을 빌려 쓴다. D에 추가되면 그걸로 교체.
    return NodeResult(Status.PARTIAL, outcome, data, run._diagnostic(basis, (), None))


def identity(pair_id: str, role: Role) -> str:
    """LiveKit participant identity 규칙 `{pair_id}:{role}` (D와 합의 필요)."""
    return f"{pair_id}:{role.value}"


def parse_identity(value: str) -> tuple[str, Role] | None:
    pair_id, _, role = value.rpartition(":")
    if not pair_id or role not in Role.__members__:
        return None
    return pair_id, Role(role)
