"""B 제어 엔드포인트 (docs/implementation-contract.md 제안 경로).

실행: uvicorn hyojason_voice.app:app --reload --port 8000  (에뮬레이터에서는 http://10.0.2.2:8000)

통화가 수락되면 STT worker를 같은 이벤트 루프의 작업으로 띄운다(_start_worker). worker가 방에서 본
참가·퇴장이 N-03 JOINED/END가 되고, 확정문이 N-06을 거쳐 speech.final이 된다.
LiveKit 웹훅(/v1/livekit/webhook)도 같은 JOINED/END로 들어온다 → worker가 죽어도 참가·퇴장은 남는다(중복은 N-03이 흡수).
ponytail: 프로세스 하나가 모든 통화의 worker를 돈다. 통화가 많아지면 LiveKit Agents dispatch로 분리.

아직 없는 것:
- D로 가는 WS 전송. 발행 이벤트는 OUTBOX에 쌓고 /v1/dev/outbox로 본다.

핸들러와 worker 콜백은 상태를 바꾸는 동안 await하지 않는다 → 이벤트 루프 하나에서 한 번에 하나씩 돈다.
ponytail: 이게 상태 잠금 역할이다. 그 사이에 await(외부 호출)가 생기면 asyncio.Lock으로 바꿀 것.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import deque
from dataclasses import asdict
from datetime import timedelta

from fastapi import FastAPI, Header, HTTPException, Request
from hyojason_session.common.enums import CallState, Role, Source
from hyojason_session.common.result import ErrorCode, NodeResult, Status, new_id
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import ActorContext
from hyojason_session.help_state import Event
from livekit import api as lk
from pydantic import BaseModel, Field

from . import worker
from .auth import Tokens
from .call import CallEndReason, CallRegistry, Operation, start_or_close_call
from .common import identity, parse_identity
from .consent import Capability, ConsentState, ConsentUpdate, Purpose, resolve_consent
from .events import MANUAL_TEST, STT, call_event, speech_final_event
from .finalize import FinalizeRequest, TurnFinalizer
from .pair import PairRegistry, PairRequest, resolve_pair
from .stt import Finality

logging.basicConfig(level=logging.WARNING)
logging.getLogger("hyojason").setLevel(logging.INFO)  # 원문은 찍지 않는다(글자 수·outcome만)
log = logging.getLogger("hyojason.app")
app = FastAPI(title="hyojason-voice (B)")
pairs = PairRegistry()
tokens = Tokens()
calls = CallRegistry()
consents: dict[str, ConsentState] = {}
finalizers: dict[str, TurnFinalizer] = {}
workers: dict[str, asyncio.Task] = {}  # call_id -> STT worker 작업
_bg: set[asyncio.Task] = set()  # 방 삭제 같은 일회성 작업 (참조를 잡아 둬야 GC되지 않는다)
# (trace_id, event). D의 Event 봉투에는 trace_id 칸이 없어 옆에 붙여 둔다.
OUTBOX: deque[tuple[str, Event]] = deque(maxlen=1000)  # ponytail: D의 WS /v1/events가 생기면 거기로 publish

_HTTP = {
    ErrorCode.UNAUTHORIZED: 403,
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.DUPLICATE: 409,
    ErrorCode.STALE_BASIS: 409,
    ErrorCode.ALREADY_ENDED: 409,
    ErrorCode.NO_CONSENT: 409,
    ErrorCode.TIMEOUT: 408,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
}
RTC_TOKEN_TTL = timedelta(minutes=10)  # 참가용. 연결된 뒤 갱신은 LiveKit SDK가 한다


def _fail(code: ErrorCode):
    raise HTTPException(_HTTP.get(code, 400), code.value)


def _reply(result: NodeResult) -> dict:
    if result.status is Status.ERROR:
        _fail(ErrorCode(result.diagnostics.terminal_error.code))
    return {
        "status": result.status.value,
        "outcome": result.outcome,
        "data": to_jsonable(result.data),
        "trace_id": result.diagnostics.trace_id,
    }


def _actor(authorization: str | None) -> ActorContext:
    """Bearer 토큰 → 확정된 페어의 구성원. 토큰이 없거나 페어가 아직 대기 중이면 403."""
    actor_id = tokens.actor_of(authorization)
    pair_id = pairs.pair_of(actor_id) if actor_id else None
    role = pairs.role_of(pair_id, actor_id) if pair_id else None
    if role is None:
        _fail(ErrorCode.UNAUTHORIZED)
    return ActorContext(actor_id, pair_id, role)


def _device(authorization: str | None) -> tuple[str, str | None]:
    """invite/claim 전용: 토큰이 있으면 그 기기, 없으면 새 기기로 토큰을 발급한다. (actor_id, 새 토큰 또는 None)
    ponytail: 토큰 없이 오면 매번 새 actor라 actor별 코드 실패 한도(MAX_CLAIM_FAILURES)를 우회할 수 있다
    (헤더를 믿던 이전 방식도 같았다). 공개 서버로 열면 IP별 속도 제한을 앞에 둘 것.
    """
    if actor_id := tokens.actor_of(authorization):
        return actor_id, None
    return tokens.issue()


def _consent(pair_id: str) -> ConsentState:
    return consents.setdefault(pair_id, ConsentState())


def _publish(event: Event, trace_id: str) -> None:
    receiver = getattr(app.state, "call_event_receiver", None)
    if receiver is None:
        OUTBOX.append((trace_id, event))
    else:
        receiver(event, trace_id)


def _ended(call, role: Role | None) -> None:
    """통화가 끝났을 때 한 번: STT 확정 경로를 닫고(B02.3) call.ended를 낸다. help.stop은 만들지 않는다."""
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


# ---- N-01 ----


class InviteBody(BaseModel):
    role: Role  # 앱 토글로 고른 내 가족 역할


@app.post("/v1/pairs/invite")
async def invite(body: InviteBody, authorization: str | None = Header(None)):
    actor_id, token = _device(authorization)
    issued = pairs.issue_invite(actor_id, body.role)
    if issued is None:  # 이미 페어가 확정됐거나 대기 중 페어에서 다른 역할 (1:1)
        _fail(ErrorCode.DUPLICATE)
    return {"pair_id": issued[0], "invite_code": issued[1]} | ({"token": token} if token else {})


class ClaimBody(BaseModel):
    role: Role
    invite_code: str | None = Field(None, pattern=r"^\d{6}$")
    pair_id: str | None = Field(None, max_length=128)


@app.post("/v1/pairs/claim")
async def claim(body: ClaimBody, authorization: str | None = Header(None)):
    """코드 입력(토큰 없음 → 새 토큰 발급) 또는 코드를 만든 쪽의 대기 확인(그때 받은 토큰)."""
    actor_id, token = _device(authorization)
    reply = _reply(resolve_pair(PairRequest(actor_id, body.role, body.invite_code, body.pair_id), pairs, trace_id=new_id("trace")))
    return reply | ({"token": token} if token else {})


# ---- N-02 ----


class ConsentBody(BaseModel):
    call_id: str | None = Field(None, max_length=128)
    enabled: bool
    consent_revision: int = Field(ge=0)  # 마지막으로 받은 서버 버전
    device_capabilities: list[Capability] = []


@app.get("/v1/consent")
async def get_consent(authorization: str | None = Header(None)):
    """STALE_BASIS를 받은 앱이 현재 동의를 다시 읽는 경로."""
    x_pair_id = _actor(authorization).pair_id
    return to_jsonable(_consent(x_pair_id).view())


@app.post("/v1/consent")
async def consent(body: ConsentBody, authorization: str | None = Header(None)):
    actor = _actor(authorization)
    x_pair_id = actor.pair_id
    upd = ConsentUpdate(
        x_pair_id, body.call_id, actor.role, Purpose.ANALYSIS, body.enabled,
        body.consent_revision, frozenset(body.device_capabilities),
    )
    # 철회 뒤 도착하는 확정문은 N-06이 최신 동의와 대조해 버린다
    call = calls.get(body.call_id) if body.call_id else None
    tid = call.trace_id if call and call.pair_id == x_pair_id else new_id("trace")  # 통화 중 철회도 같은 추적으로
    reply = _reply(resolve_consent(upd, actor, _consent(x_pair_id), trace_id=tid))
    receiver = getattr(app.state, "consent_receiver", None)
    if receiver is not None:
        receiver(actor, body.enabled)
    return reply


# ---- N-03 ----


def _transition(
    op: Operation, actor: ActorContext, call_id: str | None = None, reason: CallEndReason = CallEndReason.HANGUP,
    *, assisted_role: Role = Role.PARENT,
) -> NodeResult:
    """N-03 한 번 + 그에 따른 이벤트 발행·worker 시작. HTTP 핸들러와 worker 콜백이 같이 쓴다."""
    for c in calls.expire():  # 수락·참가 시간 초과
        _ended(c, None)
    before = calls.get(call_id) if call_id else None
    was_open = before is not None and before.state is not CallState.ENDED
    result = start_or_close_call(op, actor, calls, call_id=call_id, reason=reason)
    if was_open and before.state is CallState.ENDED:
        _ended(before, actor.role)
    if result.status is Status.ERROR:
        return result
    v, tid = result.data, result.diagnostics.trace_id
    if op is Operation.START:
        event = call_event("call.invite", actor.pair_id, v.call_id, actor.role)
        event.payload["assisted_role"] = assisted_role.value
        _publish(event, tid)
    elif op is Operation.ACCEPT:
        _publish(call_event("call.accepted", actor.pair_id, v.call_id, actor.role), tid)
        _start_worker(calls.get(v.call_id))
    elif result.outcome == "active" and v.call_id not in finalizers:
        finalizers[v.call_id] = TurnFinalizer(actor.pair_id, v.call_id)
        _publish(call_event("call.connected", actor.pair_id, v.call_id, None), tid)
    return result


def _call_op(op: Operation, actor: ActorContext, call_id: str | None = None) -> dict:
    return _reply(_transition(op, actor, call_id))


# ---- STT worker 연결 (N-03 참가·퇴장, N-04~N-06) ----


def _hooks(call_id: str) -> worker.CallHooks:
    call = calls.get(call_id)
    pair_id = call.pair_id

    def member(role: Role) -> ActorContext:
        return ActorContext(pairs.members[pair_id][role], pair_id, role)

    def joined(role: Role) -> None:
        _transition(Operation.JOINED, member(role), call_id)

    def left(role: Role) -> None:  # 앱의 /end 없이 방을 나감 = RTC 끊김
        _transition(Operation.END, member(role), call_id, CallEndReason.RTC_FAILED)

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


class StartCallBody(BaseModel):
    assisted_role: Role = Role.PARENT


@app.post("/v1/calls")
async def start_call(body: StartCallBody | None = None, authorization: str | None = Header(None)):
    assisted = body.assisted_role if body is not None else Role.PARENT
    return _reply(_transition(Operation.START, _actor(authorization), assisted_role=assisted))


@app.post("/v1/calls/{call_id}/accept")
async def accept_call(call_id: str, authorization: str | None = Header(None)):
    return _call_op(Operation.ACCEPT, _actor(authorization), call_id)


@app.post("/v1/calls/{call_id}/end")
async def end_call(call_id: str, authorization: str | None = Header(None)):
    return _call_op(Operation.END, _actor(authorization), call_id)


@app.get("/v1/calls/current")
async def current_call(authorization: str | None = Header(None)):
    """페어의 진행 중 통화(없으면 null). 앱이 수신·수락·종료를 알기 위해 짧은 주기로 묻는다.

    ponytail: 폴링. D의 WS /v1/events가 생기면 call.* 이벤트로 바꾸고 이 경로는 재연결 복구용으로만 둔다.
    """
    x_pair_id = _actor(authorization).pair_id
    for c in calls.expire():  # 응답 없는 통화를 여기서도 끝낸다
        _ended(c, None)
    cid = calls.active_by_pair.get(x_pair_id)
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


@app.post("/v1/calls/{call_id}/rtc-token")
async def rtc_token(call_id: str, authorization: str | None = Header(None)):
    """수락된 진행 중 통화의 참여자에게만, 그 통화 방(room=call_id) 마이크 발행 권한만 준다."""
    actor = _actor(authorization)
    call = calls.get(call_id)
    if call is None or call.pair_id != actor.pair_id or not call.accepted:
        _fail(ErrorCode.UNAUTHORIZED)  # 다른 페어·수락 전은 같은 오류
    if call.state is CallState.ENDED:
        _fail(ErrorCode.ALREADY_ENDED)
    url, key, secret = (os.environ.get(k) for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not (url and key and secret):
        _fail(ErrorCode.DEPENDENCY_UNAVAILABLE)
    who = identity(actor.pair_id, actor.role)
    grants = lk.VideoGrants(
        room_join=True,
        room=call_id,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=False,
        can_publish_sources=["microphone"],  # 화면·카메라 트랙 금지 (AGENTS.md)
    )
    token = lk.AccessToken(key, secret).with_identity(who).with_grants(grants).with_ttl(RTC_TOKEN_TTL).to_jwt()
    return {"url": url, "token": token, "room": call_id, "identity": who, "trace_id": call.trace_id}


# ---- dev 전용 ----


@app.post("/v1/dev/calls/{call_id}/joined")
async def dev_joined(call_id: str, authorization: str | None = Header(None)):
    """worker 없이(키 없이) 테스트할 때 참가를 손으로 알린다. 평소에는 worker가 한다."""
    return _call_op(Operation.JOINED, _actor(authorization), call_id)


class SpeechBody(BaseModel):
    call_id: str = Field(min_length=1, max_length=128)
    speaker_role: Role
    text: str = Field(min_length=1, max_length=500)


@app.post("/v1/livekit/webhook")
async def livekit_webhook(request: Request, authorization: str | None = Header(None)):
    """LiveKit 참가·퇴장 웹훅(#5). 서명(Authorization JWT의 sha256 = 본문 해시, API 키로 서명)이 맞을 때만 믿는다.

    방 이름 = call_id, identity = `{pair_id}:{role}`. 다른 페어·모르는 참가자(stt-worker 등)는 무시한다.
    """
    if not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET")):
        _fail(ErrorCode.DEPENDENCY_UNAVAILABLE)
    try:
        ev = lk.WebhookReceiver(lk.TokenVerifier()).receive((await request.body()).decode(), authorization or "")
    except Exception:
        _fail(ErrorCode.UNAUTHORIZED)
    if ev.event not in ("participant_joined", "participant_left"):
        return {"ignored": ev.event}
    who, call = parse_identity(ev.participant.identity), calls.get(ev.room.name)
    if who is None or call is None or call.pair_id != who[0]:
        return {"ignored": "unknown_participant"}
    member = ActorContext(pairs.members[call.pair_id][who[1]], call.pair_id, who[1])
    if ev.event == "participant_joined":
        r = _transition(Operation.JOINED, member, call.call_id)
    else:  # 앱의 /end 없이 나감 = RTC 끊김 (이미 끝난 통화면 N-03이 그대로 둔다)
        r = _transition(Operation.END, member, call.call_id, CallEndReason.RTC_FAILED)
    return {"outcome": r.outcome}


@app.post("/v1/dev/speech-final")
async def dev_speech_final(body: SpeechBody, authorization: str | None = Header(None)):
    """텍스트 입력 스텁. C·D가 음성 없이 통합할 수 있게 N-06을 거쳐 speech.final을 낸다.

    실제 STT처럼 활성 통화 + 양쪽 동의가 있어야 통과한다.
    """
    x_pair_id = _actor(authorization).pair_id
    fin = finalizers.get(body.call_id)
    if fin is None or fin.pair_id != x_pair_id:
        _fail(ErrorCode.STALE_BASIS)
    view = _consent(x_pair_id).view()
    if not view.stt_allowed:
        _fail(ErrorCode.NO_CONSENT)
    seq = fin.last_seq.get(body.speaker_role, 0) + 1
    req = FinalizeRequest(
        body.call_id, f"manual-{body.speaker_role.value}-{seq}", seq, body.speaker_role,
        Finality.FINAL, body.text, None, view.consent_revision,
    )
    tid = calls.get(body.call_id).trace_id  # 통화와 같은 trace_id로 묶는다
    result = fin.finalize(req, view, trace_id=tid, source=Source.FIXTURE)
    if result.outcome == "final_committed":
        _publish(speech_final_event(x_pair_id, body.call_id, result.data, MANUAL_TEST), tid)
    return _reply(result)


@app.get("/v1/dev/outbox")
async def dev_outbox():
    return [{"trace_id": tid, **asdict(e)} for tid, e in OUTBOX]
