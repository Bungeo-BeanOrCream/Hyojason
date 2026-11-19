"""N-01 resolve_pair: 일회용 초대 코드로 부모 1 · 자녀 1 페어를 만든다.

가족 역할은 앱의 토글로 각자 고른다. 어느 역할이든 코드를 만들 수 있고(만든 쪽 승인),
코드를 쓰는 쪽은 반대 역할이어야 한다(쓴 쪽 승인). 같은 역할 둘은 페어가 되지 않는다.
D의 PairDirectory(role_of)도 구현해서 mocks.StaticPairDirectory 자리에 꽂을 수 있다.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus, new_id

INVITE_TTL_S = 600  # 초대 코드 유효 시간
MAX_CLAIM_FAILURES = 5  # 6자리 코드 무차별 대입 방지. 넘으면 그 actor의 코드 사용을 막는다


class ApprovalState(StrEnum):
    PAIRED = "paired"
    PENDING = "pending"
    DECLINED = "declined"


@dataclass(frozen=True)
class PairRequest:
    actor_id: str  # 세션 인증 결과. 클라이언트 값 아님
    role: Role
    invite_code: str | None = None
    pair_id: str | None = None


@dataclass(frozen=True)
class PairView:
    pair_id: str | None
    role: Role
    peer_alias: str | None
    peer_available: bool
    approval_state: ApprovalState


@dataclass
class _Invite:
    pair_id: str
    expires_at: float
    used: bool = False


@dataclass
class PairRegistry:
    """메모리 저장소. ponytail: 재시작하면 사라진다. 시연 뒤에도 유지가 필요하면 DB로."""

    clock: Callable[[], float] = time.time
    members: dict[str, dict[Role, str]] = field(default_factory=dict)  # pair_id -> {role: actor_id}
    invites: dict[str, _Invite] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)

    def pair_of(self, actor_id: str) -> str | None:
        return next((pid for pid, m in self.members.items() if actor_id in m.values()), None)

    def issue_invite(self, actor_id: str, role: Role) -> tuple[str, str] | None:
        """토글로 고른 역할로 초대 코드를 만든다. (pair_id, code). 이미 페어가 확정됐거나
        대기 중인 페어에서 다른 역할이었으면 None(1:1).

        승인 대기 중인 페어가 있으면 같은 페어에 새 코드를 주고 이전 코드는 무효로 한다.
        """
        now = self.clock()
        pair_id = self.pair_of(actor_id)
        if pair_id is not None:
            if len(self.members[pair_id]) == 2 or self.members[pair_id].get(role) != actor_id:
                return None
            for inv in self.invites.values():
                if inv.pair_id == pair_id:
                    inv.used = True
        else:
            pair_id = new_id("pair")
            self.members[pair_id] = {role: actor_id}
        # 만료·사용된 코드 정리 후, 살아 있는 코드와 겹치지 않게 뽑는다
        self.invites = {c: i for c, i in self.invites.items() if not i.used and i.expires_at >= now}
        code = f"{secrets.randbelow(10**6):06d}"
        while code in self.invites:
            code = f"{secrets.randbelow(10**6):06d}"
        self.invites[code] = _Invite(pair_id, now + INVITE_TTL_S)
        return pair_id, code

    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        """D PairDirectory. 확정된 페어의 구성원만 역할을 돌려준다."""
        m = self.members.get(pair_id, {})
        if len(m) < 2:
            return None
        return next((r for r, uid in m.items() if uid == user_id), None)


def resolve_pair(
    req: PairRequest, registry: PairRegistry, *, trace_id: str, source: Source = Source.LIVE
) -> NodeResult[PairView]:
    run = NodeRun("N-01", trace_id, source)
    basis = Basis(pair_id=req.pair_id)

    if req.invite_code is not None:
        inv = registry.invites.get(req.invite_code)
        # 만료·재사용·없는 코드·만든 쪽과 같은 역할·이미 다른 페어 소속을 같은 오류로 묶어
        # 상대 존재를 드러내지 않는다.
        if (
            registry.failures.get(req.actor_id, 0) >= MAX_CLAIM_FAILURES
            or inv is None
            or inv.used
            or inv.expires_at < registry.clock()
            or req.role in registry.members[inv.pair_id]
            or registry.pair_of(req.actor_id) is not None
        ):
            registry.failures[req.actor_id] = registry.failures.get(req.actor_id, 0) + 1
            return run.error(ErrorCode.UNAUTHORIZED, "consume_code", basis)
        inv.used = True
        registry.failures.pop(req.actor_id, None)
        registry.members[inv.pair_id][req.role] = req.actor_id
        run.step("consume_code", StepStatus.SUCCESS)
        pair_id = inv.pair_id
    elif req.pair_id is not None:
        m = registry.members.get(req.pair_id)
        if m is None or m.get(req.role) != req.actor_id:
            return run.error(ErrorCode.UNAUTHORIZED, "lookup_pair", basis)
        run.step("lookup_pair", StepStatus.SUCCESS)
        pair_id = req.pair_id
    else:
        return run.error(ErrorCode.INVALID_INPUT, "validate", basis)

    paired = len(registry.members[pair_id]) == 2
    view = PairView(
        pair_id=pair_id,
        role=req.role,
        peer_alias=None,  # 앱이 테스트 사진·별칭을 로컬로 쓴다
        peer_available=paired,
        approval_state=ApprovalState.PAIRED if paired else ApprovalState.PENDING,
    )
    return run.success(view.approval_state.value, view, Basis(pair_id=pair_id))
