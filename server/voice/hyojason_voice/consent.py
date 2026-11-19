"""N-02 resolve_consent: 역할별 AI 처리 동의와 부모 기기 권한을 기능별로 나눠 계산한다.

통화 참여와 AI 처리 동의는 별개다. 동의가 없어도 통화는 그대로 둔다(voice_only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus
from hyojason_session.common.types import ActorContext


class Purpose(StrEnum):
    ANALYSIS = "analysis"  # D consent.updated의 purpose 값과 같다


class Capability(StrEnum):
    """Android가 로컬로 확인한 권한. 캡처·표시는 부모 기기 값만 의미가 있다."""

    MIC = "MIC"
    CAPTURE = "CAPTURE"
    OVERLAY = "OVERLAY"


@dataclass(frozen=True)
class ConsentUpdate:
    pair_id: str
    call_id: str | None
    role: Role
    purpose: Purpose
    enabled: bool
    consent_revision: int  # 클라이언트가 마지막으로 본 서버 버전(basis). 올리는 건 서버
    device_capabilities: frozenset[Capability]


@dataclass(frozen=True)
class ConsentView:
    agent_allowed: bool
    stt_allowed: bool
    capture_allowed: bool
    overlay_allowed: bool
    consent_revision: int


@dataclass
class ConsentState:
    """페어 하나의 동의 상태."""

    revision: int = 0
    enabled: dict[Role, bool] = field(default_factory=dict)
    caps: dict[Role, frozenset[Capability]] = field(default_factory=dict)

    def view(self) -> ConsentView:
        agent = self.enabled.get(Role.PARENT, False) and self.enabled.get(Role.CHILD, False)
        parent_caps = self.caps.get(Role.PARENT, frozenset())
        return ConsentView(
            agent_allowed=agent,
            stt_allowed=agent,
            capture_allowed=agent and Capability.CAPTURE in parent_caps,
            overlay_allowed=agent and Capability.OVERLAY in parent_caps,
            consent_revision=self.revision,
        )


def resolve_consent(
    upd: ConsentUpdate,
    actor: ActorContext,
    state: ConsentState,
    *,
    trace_id: str,
    source: Source = Source.LIVE,
) -> NodeResult[ConsentView]:
    run = NodeRun("N-02", trace_id, source)
    basis = Basis(pair_id=upd.pair_id, call_id=upd.call_id, consent_revision=upd.consent_revision)

    if actor.pair_id != upd.pair_id or actor.role is not upd.role:
        return run.error(ErrorCode.UNAUTHORIZED, "check_scope", basis)
    if upd.consent_revision != state.revision:  # 다른 쪽이 먼저 바꿨다 → 쓰지 않고 재조회 유도
        return run.error(ErrorCode.STALE_BASIS, "check_scope", basis)
    run.step("check_scope", StepStatus.SUCCESS)

    changed = (state.enabled.get(upd.role), state.caps.get(upd.role)) != (upd.enabled, upd.device_capabilities)
    if changed:  # 같은 값 재전송은 버전을 올리지 않는다
        state.revision += 1
        state.enabled[upd.role] = upd.enabled
        state.caps[upd.role] = upd.device_capabilities
    view = state.view()
    run.step("compute_view", StepStatus.SUCCESS)

    ok = view.agent_allowed and view.capture_allowed and view.overlay_allowed
    return run.success("allowed" if ok else "blocked", view, basis)
