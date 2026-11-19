"""N-03 start_or_close_call (서버 쪽): 발신 → 수락 → 양쪽 RTC 참가 → 종료.

발신은 부모·자녀 누구나 한다. 수락은 발신자의 상대 역할만 한다.
발신 방향은 안내 대상(assisted_role, #6)과 별개다 — 여기서는 다루지 않는다.

RTC 연결 상태만 다룬다. 도움 종료(help.stop)는 만들지 않는다 — N-11/N-22 몫.
스피커 전환은 Android가 한다(call_id당 1회, 이어폰·Bluetooth면 유지). 서버는 SYSTEM으로만 기록.
LiveKit 토큰 발급은 API 키가 필요해 rtc_token.py로 분리했다(아직 미구현).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from hyojason_session.common.enums import CallState, Role, Source
from hyojason_session.common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus, new_id
from hyojason_session.common.types import ActorContext

ACCEPT_TIMEOUT_S = 30  # 발신부터 양쪽 RTC 참가까지 한도. 시연 리허설에서 조정


def new_trace_id() -> str:
    # ponytail: 지금은 B가 발급한다. D가 발급하기로 정하면(#5) 이 함수만 바꾼다.
    return new_id("trace")


class Operation(StrEnum):
    START = "START"  # 발신 (누구나)
    ACCEPT = "ACCEPT"  # 상대 수락
    JOINED = "JOINED"  # LiveKit 참가 콜백(역할별)
    END = "END"  # 끊기 또는 RTC 종료 콜백


class AudioRoute(StrEnum):
    SYSTEM = "SYSTEM"
    SPEAKER = "SPEAKER"
    HEADSET = "HEADSET"


class CallEndReason(StrEnum):
    HANGUP = "HANGUP"
    ACCEPT_TIMEOUT = "ACCEPT_TIMEOUT"
    RTC_FAILED = "RTC_FAILED"


@dataclass(frozen=True)
class CallView:
    call_id: str
    call_state: CallState
    connected_roles: tuple[Role, ...]
    audio_route: AudioRoute
    end_reason: CallEndReason | None


@dataclass
class _Call:
    call_id: str
    pair_id: str
    created_at: float
    caller_role: Role
    trace_id: str  # 이 통화의 N-03~N-06 결과와 call.*·speech.final을 묶는 실행 추적 ID (#5)
    state: CallState = CallState.RINGING
    accepted: bool = False
    connected: set[Role] = field(default_factory=set)
    end_reason: CallEndReason | None = None

    def view(self) -> CallView:
        return CallView(
            self.call_id,
            self.state,
            tuple(r for r in (Role.PARENT, Role.CHILD) if r in self.connected),
            AudioRoute.SYSTEM,
            self.end_reason,
        )


@dataclass
class CallRegistry:
    """페어당 진행 중 통화 1개. ponytail: 메모리 저장, 서버 재시작 시 통화도 사라진다."""

    clock: Callable[[], float] = time.time
    calls: dict[str, _Call] = field(default_factory=dict)
    active_by_pair: dict[str, str] = field(default_factory=dict)

    def get(self, call_id: str) -> _Call | None:
        return self.calls.get(call_id)

    def expire(self) -> list[_Call]:
        """한도 안에 ACTIVE가 못 된 통화를 끝낸다. 호출자는 돌려받은 통화마다 call.ended를 낸다.

        ponytail: 요청이 올 때만 검사한다. 아무 요청이 없으면 늦게 끝난다 — 필요하면 주기 작업으로.
        """
        now = self.clock()
        out = []
        for cid in list(self.active_by_pair.values()):
            c = self.calls[cid]
            if c.state is CallState.RINGING and now - c.created_at > ACCEPT_TIMEOUT_S:
                _end(self, c, CallEndReason.RTC_FAILED if c.accepted else CallEndReason.ACCEPT_TIMEOUT)
                out.append(c)
        return out


def start_or_close_call(
    op: Operation,
    actor: ActorContext,
    registry: CallRegistry,
    *,
    call_id: str | None = None,
    reason: CallEndReason = CallEndReason.HANGUP,
    source: Source = Source.LIVE,
) -> NodeResult[CallView]:
    """actor는 인증 계층이 준 값(이미 N-01 paired 확인). JOINED는 LiveKit 웹훅의 identity에서 만든다.

    trace_id는 START에서 통화마다 하나 만들고, 같은 통화의 이후 호출은 그 값을 그대로 쓴다.
    """
    registry.expire()  # 응답 없는 통화가 새 발신을 막지 않게
    call = registry.get(call_id) if call_id else None
    own = call is not None and call.pair_id == actor.pair_id
    # 다른 페어의 통화를 건드린 요청에는 그 통화의 trace_id를 쓰지 않는다(노출 금지)
    run = NodeRun("N-03", call.trace_id if own else new_trace_id(), source)
    basis = Basis(pair_id=actor.pair_id, call_id=call_id)

    if op is Operation.START:
        if actor.pair_id in registry.active_by_pair:  # 이미 열린 통화
            return run.error(ErrorCode.DUPLICATE, "check_actor", basis)
        call = _Call(new_id("call"), actor.pair_id, registry.clock(), actor.role, run.trace_id)
        registry.calls[call.call_id] = call
        registry.active_by_pair[actor.pair_id] = call.call_id
        run.step("create_call", StepStatus.SUCCESS)
        return run.success("waiting", call.view(), Basis(pair_id=actor.pair_id, call_id=call.call_id))

    if not own:
        return run.error(ErrorCode.UNAUTHORIZED, "check_actor", basis)
    run.step("check_actor", StepStatus.SUCCESS)

    if call.state is CallState.ENDED:
        # 같은 종료 콜백 재수신은 no-op. 종료 뒤 수락·참가는 거절.
        if op is Operation.END:
            return run.success("ended", call.view(), basis)
        timed_out = call.end_reason in (CallEndReason.ACCEPT_TIMEOUT, CallEndReason.RTC_FAILED)
        return run.error(ErrorCode.TIMEOUT if timed_out else ErrorCode.ALREADY_ENDED, "check_state", basis)

    if op is Operation.END:
        _end(registry, call, reason)
        return run.success("ended", call.view(), basis)

    if op is Operation.ACCEPT:
        if actor.role is call.caller_role:  # 자기 발신은 수락 불가
            return run.error(ErrorCode.UNAUTHORIZED, "accept", basis)
        call.accepted = True  # 중복 수락은 새 방·토큰을 만들지 않는다
        run.step("accept", StepStatus.SUCCESS)
        return run.success("waiting", call.view(), basis)

    # JOINED
    if not call.accepted:
        return run.error(ErrorCode.UNAUTHORIZED, "join", basis)
    call.connected.add(actor.role)
    run.step("join", StepStatus.SUCCESS, count=len(call.connected))
    if len(call.connected) == 2:
        call.state = CallState.ACTIVE
        return run.success("active", call.view(), basis)
    return run.success("waiting", call.view(), basis)


def _end(registry: CallRegistry, call: _Call, reason: CallEndReason) -> None:
    call.state = CallState.ENDED
    call.end_reason = reason
    registry.active_by_pair.pop(call.pair_id, None)
