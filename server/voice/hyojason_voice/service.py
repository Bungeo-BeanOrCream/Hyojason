"""B 판단 함수 묶음 (#10 합의: B는 함수만 주고 D가 자기 FastAPI 라우트에 연결한다).

프레임워크와 무관하다. 오류는 `ServiceError(ErrorCode)`로 올리고, D는 자기 오류 봉투로 바꾸면 된다.
`app.py`는 이 함수들을 그대로 감싼 B의 임시 라우트다(D 연결 전 dev·AVD 확인용).

D가 쓸 것:
- 인증: `authenticate(token)`·`role_of(pair_id, user_id)` → D의 MockTokenAuthenticator 자리에 이 모듈을 그대로 넣는다.
- 이벤트: `set_event_sink(fn)` → B가 내는 call.*·speech.final을 D 쪽(예: hub.apply_server_event)으로 보낸다.
- N-01 `invite`·`claim`·`unpair`, N-02 `get_consent`·`set_consent`, N-03 `call_op`·`current_call`·`rtc_token`,
  LiveKit 웹훅 `livekit_webhook`, dev 텍스트 입력 `dev_speech_final`.

페어는 통화가 끝나도 유지된다. 끊는 것은 `unpair`(누군가 '가족 연결 끊기')뿐이다.

통화가 수락되면 STT worker를 같은 이벤트 루프의 작업으로 띄운다(_start_worker). worker가 방에서 본
참가·퇴장이 N-03 JOINED/END가 되고, 확정문이 N-06을 거쳐 speech.final이 된다.
LiveKit 웹훅도 같은 JOINED/END로 들어온다 → worker가 죽어도 참가·퇴장은 남는다(중복은 N-03이 흡수).
ponytail: 프로세스 하나가 모든 통화의 worker를 돈다. 통화가 많아지면 LiveKit Agents dispatch로 분리.

함수들은 상태를 바꾸는 동안 await하지 않는다 → 이벤트 루프 하나에서 한 번에 하나씩 돈다.
ponytail: 이게 상태 잠금 역할이다. 그 사이에 await(외부 호출)가 생기면 asyncio.Lock으로 바꿀 것.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from collections.abc import Callable
from datetime import timedelta

from hyojason_session.common.enums import CallState, Role, Source
from hyojason_session.common.result import ErrorCode, NodeResult, Status, new_id
from hyojason_session.common.types import ActorContext
from hyojason_session.help_state import Event
from livekit import api as lk

from . import worker
from .auth import BEARER, Tokens
from .call import CallEndReason, CallRegistry, Operation, start_or_close_call
from .common import identity, parse_identity
from .consent import Capability, ConsentState, ConsentUpdate, ConsentView, Purpose, resolve_consent
from .events import MANUAL_TEST, STT, call_event, speech_final_event
from .finalize import FinalizeRequest, TurnFinalizer
from .pair import PairRegistry, PairRequest, resolve_pair
from .stt import Finality

log = logging.getLogger("hyojason.service")

pairs = PairRegistry()
calls = CallRegistry()
tokens = Tokens()
consents: dict[str, ConsentState] = {}
finalizers: dict[str, TurnFinalizer] = {}
workers: dict[str, asyncio.Task] = {}  # call_id -> STT worker 작업
_bg: set[asyncio.Task] = set()  # 방 삭제 같은 일회성 작업 (참조를 잡아 둬야 GC되지 않는다)
# (trace_id, event). D의 Event 봉투에는 trace_id 칸이 없어 옆에 붙여 둔다. 기본 sink가 여기 쌓는다(dev).
OUTBOX: deque[tuple[str, Event]] = deque(maxlen=1000)
RTC_TOKEN_TTL = timedelta(minutes=10)  # 참가용. 연결된 뒤 갱신은 LiveKit SDK가 한다


class ServiceError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


def _fail(code: ErrorCode):
    raise ServiceError(code)


# ---- 이벤트 출구 ----

_sink: Callable[[str, Event], None] = lambda trace_id, event: OUTBOX.append((trace_id, event))


def set_event_sink(fn: Callable[[str, Event], None]) -> Callable[[str, Event], None]:
    """B 이벤트의 수신 함수를 연결하고 런타임 종료 시 복원할 기존 함수를 반환한다."""
    global _sink
    previous = _sink
    _sink = fn
    return previous


def _publish(event: Event, trace_id: str) -> None:
    _sink(trace_id, event)


# ---- 인증 (B-01, #10) ----


def authenticate(token: str | None) -> ActorContext | None:
    """D의 MockTokenAuthenticator.authenticate와 같은 모양. token은 `Bearer ` 뺀 값."""
    actor_id = tokens.actor_of(BEARER + token) if token else None
    pair_id = pairs.pair_of(actor_id) if actor_id else None
    role = pairs.role_of(pair_id, actor_id) if pair_id else None
    return ActorContext(actor_id, pair_id, role) if role else None


def role_of(pair_id: str, user_id: str) -> Role | None:
    """D PairDirectory (N-13)."""
    return pairs.role_of(pair_id, user_id)


def actor(authorization: str | None) -> ActorContext:
    """`Authorization` 헤더 값 → 확정된 페어의 구성원. 토큰이 없거나 페어가 아직 대기 중이면 UNAUTHORIZED."""
    who = authenticate(authorization[len(BEARER):]) if authorization and authorization.startswith(BEARER) else None
    if who is None:
        _fail(ErrorCode.UNAUTHORIZED)
    return who


def _device(authorization: str | None) -> tuple[str, str | None]:
    """invite/claim 전용: 토큰이 있으면 그 기기, 없으면 새 기기로 토큰을 발급한다. (actor_id, 새 토큰 또는 None)
    ponytail: 토큰 없이 오면 매번 새 actor라 actor별 코드 실패 한도(MAX_CLAIM_FAILURES)를 우회할 수 있다
    (헤더를 믿던 이전 방식도 같았다). 공개 서버로 열면 IP별 속도 제한을 앞에 둘 것.
    """
    if actor_id := tokens.actor_of(authorization):
        return actor_id, None
    return tokens.issue()


# ---- N-01 ----


def invite(authorization: str | None, role: Role) -> dict:
    """코드 만들기. {pair_id, invite_code[, token]} — token은 처음 오는 기기에만."""
    actor_id, token = _device(authorization)
    issued = pairs.issue_invite(actor_id, role)
    if issued is None:  # 이미 페어가 확정됐거나 대기 중 페어에서 다른 역할 (1:1)
        _fail(ErrorCode.DUPLICATE)
    return {"pair_id": issued[0], "invite_code": issued[1]} | ({"token": token} if token else {})


def claim(
    authorization: str | None, role: Role, invite_code: str | None = None, pair_id: str | None = None
) -> tuple[NodeResult, str | None]:
    """코드 입력(토큰 없음 → 새 토큰) 또는 코드를 만든 쪽의 대기 확인(그때 받은 토큰). (결과, 새 토큰 또는 None)
    실패하면 토큰을 내주지 않는다(호출 쪽이 결과를 먼저 본다)."""
    actor_id, token = _device(authorization)
    result = resolve_pair(PairRequest(actor_id, role, invite_code, pair_id), pairs, trace_id=new_id("trace"))
    return result, token if result.status is not Status.ERROR else None


def unpair(who: ActorContext) -> None:
    """'가족 연결 끊기'. 진행 중 통화를 끝내고(HANGUP) 페어·동의를 지운다.
    두 기기 모두 다음 요청부터 UNAUTHORIZED → 앱은 연결 화면으로 돌아간다. 다시 쓰려면 새 코드로 연결."""
    if cid := calls.active_by_pair.get(who.pair_id):
        call_op(Operation.END, who, cid)
    retire_pair(who.pair_id)
    log.info("페어 해제 %s (by %s)", who.pair_id, who.role.value)


def retire_pair(pair_id: str) -> None:
    """종료된 페어의 인증 범위, 초대와 동의를 한 곳에서 정리한다."""
    pairs.members.pop(pair_id, None)
    pairs.invites = {code: invite for code, invite in pairs.invites.items() if invite.pair_id != pair_id}
    consents.pop(pair_id, None)


# ---- N-02 ----


def _consent(pair_id: str) -> ConsentState:
    return consents.setdefault(pair_id, ConsentState())


def get_consent(who: ActorContext) -> ConsentView:
    return _consent(who.pair_id).view()


def consenting_roles(pair_id: str) -> frozenset[Role]:
    state = consents.get(pair_id)
    return frozenset(role for role, enabled in state.enabled.items() if enabled) if state else frozenset()


def set_consent(
    who: ActorContext, enabled: bool, consent_revision: int,
    device_capabilities: frozenset[Capability] = frozenset(), call_id: str | None = None,
) -> NodeResult:
    upd = ConsentUpdate(who.pair_id, call_id, who.role, Purpose.ANALYSIS, enabled, consent_revision, device_capabilities)
    # 철회 뒤 도착하는 확정문은 N-06이 최신 동의와 대조해 버린다
    call = calls.get(call_id) if call_id else None
    tid = call.trace_id if call and call.pair_id == who.pair_id else new_id("trace")  # 통화 중 철회도 같은 추적으로
    return resolve_consent(upd, who, _consent(who.pair_id), trace_id=tid)


# ---- N-03 ----


def _ended(call, role: Role | None) -> None:
    """통화가 끝났을 때 한 번: STT 확정 경로를 닫고(B02.3) call.ended를 낸다. help.stop은 만들지 않는다.
    페어는 그대로 둔다(통화가 끝나도 가족 연결은 유지)."""
    if call.call_id in finalizers:
        finalizers[call.call_id].close()
    if task := workers.pop(call.call_id, None):
        task.cancel()  # 구독 해제·STT 스트림 닫기·버퍼 폐기는 worker.run의 finally
    _close_room(call.call_id)
    _publish(call_event("call.ended", call.pair_id, call.call_id, role, call.end_reason), call.trace_id)


def _close_room(call_id: str) -> None:
    """LiveKit 방을 지워 남아 있던 기기도 끊기게 한다(앱은 Disconnected를 받고 서비스를 멈춘다)."""
    if any(not os.environ.get(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")):
        return  # 키 없는 dev·테스트

    async def delete() -> None:
        url = os.environ["LIVEKIT_URL"].replace("wss://", "https://", 1)
        try:
            async with lk.LiveKitAPI(url) as api:
                await api.room.delete_room(lk.DeleteRoomRequest(room=call_id))
        except Exception as e:  # 이미 없는 방 등. 통화 종료 자체는 이미 끝났다
            log.warning("방 삭제 실패 %s: %s", call_id, type(e).__name__)

    t = asyncio.get_running_loop().create_task(delete())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def call_op(
    op: Operation, who: ActorContext, call_id: str | None = None, reason: CallEndReason = CallEndReason.HANGUP,
    *, assisted_role: Role = Role.PARENT,
) -> NodeResult:
    """N-03 한 번 + 그에 따른 이벤트 발행·worker 시작. START(발신)·ACCEPT·JOINED·END. 라우트와 worker 콜백이 같이 쓴다."""
    for c in calls.expire():  # 수락·참가 시간 초과
        _ended(c, None)
    before = calls.get(call_id) if call_id else None
    was_open = before is not None and before.state is not CallState.ENDED
    result = start_or_close_call(op, who, calls, call_id=call_id, reason=reason)
    if was_open and before.state is CallState.ENDED:
        _ended(before, who.role)
    if result.status is Status.ERROR:
        return result
    v, tid = result.data, result.diagnostics.trace_id
    if op is Operation.START:
        event = call_event("call.invite", who.pair_id, v.call_id, who.role)
        event.payload["assisted_role"] = assisted_role.value
        _publish(event, tid)
    elif op is Operation.ACCEPT:
        _publish(call_event("call.accepted", who.pair_id, v.call_id, who.role), tid)
        _start_worker(calls.get(v.call_id))
    elif result.outcome == "active" and v.call_id not in finalizers:
        finalizers[v.call_id] = TurnFinalizer(who.pair_id, v.call_id)
        _publish(call_event("call.connected", who.pair_id, v.call_id, None), tid)
    return result


def current_call(who: ActorContext) -> dict | None:
    """페어의 진행 중 통화(없으면 None). 앱이 수신·수락·종료를 알기 위해 짧은 주기로 묻는다.
    ponytail: 폴링. D의 WS /v1/events에 붙으면 call.* 이벤트로 바꾸고 이 함수는 재연결 복구용으로만 둔다."""
    for c in calls.expire():  # 응답 없는 통화를 여기서도 끝낸다
        _ended(c, None)
    cid = calls.active_by_pair.get(who.pair_id)
    call = calls.get(cid) if cid else None
    if call is None:
        return None
    v = call.view()
    return {
        "call_id": call.call_id,
        "call_state": v.call_state.value,
        "caller_role": call.caller_role.value,
        "accepted": call.accepted,
        "connected_roles": [r.value for r in v.connected_roles],
    }


def rtc_token(who: ActorContext, call_id: str) -> dict:
    """수락된 진행 중 통화의 참여자에게만, 그 통화 방(room=call_id) 마이크 발행 권한만 준다."""
    call = calls.get(call_id)
    if call is None or call.pair_id != who.pair_id or not call.accepted:
        _fail(ErrorCode.UNAUTHORIZED)  # 다른 페어·수락 전은 같은 오류
    if call.state is CallState.ENDED:
        _fail(ErrorCode.ALREADY_ENDED)
    url, key, secret = (os.environ.get(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not (url and key and secret):
        _fail(ErrorCode.DEPENDENCY_UNAVAILABLE)
    ident = identity(who.pair_id, who.role)
    grants = lk.VideoGrants(
        room_join=True,
        room=call_id,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=False,
        can_publish_sources=["microphone"],  # 화면·카메라 트랙 금지 (AGENTS.md)
    )
    token = lk.AccessToken(key, secret).with_identity(ident).with_grants(grants).with_ttl(RTC_TOKEN_TTL).to_jwt()
    return {"url": url, "token": token, "room": call_id, "identity": ident, "trace_id": call.trace_id}


def livekit_webhook(body: str, authorization: str | None) -> dict:
    """LiveKit 참가·퇴장 웹훅(#5). 서명(Authorization JWT의 sha256 = 본문 해시, API 키로 서명)이 맞을 때만 믿는다.
    방 이름 = call_id, identity = `{pair_id}:{role}`. 다른 페어·모르는 참가자(stt-worker 등)는 무시한다."""
    if not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET")):
        _fail(ErrorCode.DEPENDENCY_UNAVAILABLE)
    try:
        ev = lk.WebhookReceiver(lk.TokenVerifier()).receive(body, authorization or "")
    except Exception:
        _fail(ErrorCode.UNAUTHORIZED)
    if ev.event not in ("participant_joined", "participant_left"):
        return {"ignored": ev.event}
    who, call = parse_identity(ev.participant.identity), calls.get(ev.room.name)
    if who is None or call is None or call.pair_id != who[0] or call.pair_id not in pairs.members:
        return {"ignored": "unknown_participant"}
    member = ActorContext(pairs.members[call.pair_id][who[1]], call.pair_id, who[1])
    if ev.event == "participant_joined":
        r = call_op(Operation.JOINED, member, call.call_id)
    else:  # 앱의 /end 없이 나감 = RTC 끊김 (이미 끝난 통화면 N-03이 그대로 둔다)
        r = call_op(Operation.END, member, call.call_id, CallEndReason.RTC_FAILED)
    return {"outcome": r.outcome}


# ---- STT worker 연결 (N-03 참가·퇴장, N-04~N-06) ----


def _hooks(call_id: str) -> worker.CallHooks:
    call = calls.get(call_id)
    pair_id = call.pair_id

    def member(role: Role) -> ActorContext:
        return ActorContext(pairs.members[pair_id][role], pair_id, role)

    def joined(role: Role) -> None:
        if pair_id in pairs.members:
            call_op(Operation.JOINED, member(role), call_id)

    def left(role: Role) -> None:  # 앱의 /end 없이 방을 나감 = RTC 끊김
        if pair_id in pairs.members:
            call_op(Operation.END, member(role), call_id, CallEndReason.RTC_FAILED)

    def consent():
        return _consent(pair_id).view()

    def final(role: Role, turn_id: str, seq: int, text: str, ended_at: float, rev: int) -> None:
        fin = finalizers.get(call_id)
        if fin is None:
            return  # 아직 양쪽이 다 안 들어왔다(ACTIVE 전) → 버린다
        req = FinalizeRequest(call_id, turn_id, seq, role, Finality.FINAL, text, ended_at, rev)
        result = fin.finalize(req, consent(), trace_id=call.trace_id, source=Source.LIVE)
        log.info("[%s] N-06 %s", role.value, result.outcome or result.diagnostics.terminal_error.code)
        if result.outcome == "final_committed":
            _publish(speech_final_event(pair_id, call_id, result.data, STT), call.trace_id)

    return worker.CallHooks(joined, left, consent, final)


def _start_worker(call) -> None:
    if call is None or call.call_id in workers:
        return  # 중복 수락은 worker를 또 띄우지 않는다
    if missing := worker.missing_env():
        log.warning("STT worker 안 띄움: 환경변수 없음 %s (통화는 음성만)", ",".join(missing))
        return

    async def run() -> None:
        try:
            await worker.run(call.call_id, call.pair_id, _hooks(call.call_id))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # LiveKit 접속 실패 등. 통화 자체는 앱끼리 계속된다
            log.warning("STT worker 실패 %s: %s", call.call_id, type(e).__name__)
        finally:
            workers.pop(call.call_id, None)

    workers[call.call_id] = asyncio.create_task(run())


# ---- dev 전용 ----


def dev_speech_final(who: ActorContext, call_id: str, speaker_role: Role, text: str) -> NodeResult:
    """텍스트 입력 스텁. C·D가 음성 없이 통합할 수 있게 N-06을 거쳐 speech.final을 낸다.
    실제 STT처럼 활성 통화 + 양쪽 동의가 있어야 통과한다."""
    fin = finalizers.get(call_id)
    if fin is None or fin.pair_id != who.pair_id:
        _fail(ErrorCode.STALE_BASIS)
    view = _consent(who.pair_id).view()
    if not view.stt_allowed:
        _fail(ErrorCode.NO_CONSENT)
    seq = fin.last_seq.get(speaker_role, 0) + 1
    req = FinalizeRequest(
        call_id, f"manual-{speaker_role.value}-{seq}", seq, speaker_role,
        Finality.FINAL, text, None, view.consent_revision,
    )
    tid = calls.get(call_id).trace_id  # 통화와 같은 trace_id로 묶는다
    result = fin.finalize(req, view, trace_id=tid, source=Source.FIXTURE)
    if result.outcome == "final_committed":
        _publish(speech_final_event(who.pair_id, call_id, result.data, MANUAL_TEST), tid)
    return result
