"""B 임시 라우트: service.py의 판단 함수를 FastAPI로 감싼 것뿐이다 (D 서버에 연결되기 전 dev·AVD 확인용).

#10 합의대로 실제 라우트는 D의 `server/session` 서버가 같은 함수를 불러 만든다. 여기에는 판단 로직을 두지 않는다.
실행: uvicorn hyojason_voice.app:app --reload --port 8000  (에뮬레이터에서는 http://10.0.2.2:8000)
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from hyojason_session.common.enums import Role
from hyojason_session.common.result import ErrorCode, NodeResult, Status
from hyojason_session.common.serialize import to_jsonable
from pydantic import BaseModel, Field

from . import service
from .call import Operation
from .consent import Capability
from .service import OUTBOX, ServiceError, _hooks, calls, consents, finalizers, lk, pairs, workers  # noqa: F401 (테스트가 씀)

logging.basicConfig(level=logging.WARNING)
logging.getLogger("hyojason").setLevel(logging.INFO)  # 원문은 찍지 않는다(글자 수·outcome만)
app = FastAPI(title="hyojason-voice (B)")

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
Auth = Header(None, alias="Authorization")


@app.exception_handler(ServiceError)
async def _service_error(request: Request, e: ServiceError):
    return JSONResponse(status_code=_HTTP.get(e.code, 400), content={"detail": e.code.value})


def _reply(result: NodeResult) -> dict:
    if result.status is Status.ERROR:
        code = ErrorCode(result.diagnostics.terminal_error.code)
        raise HTTPException(_HTTP.get(code, 400), code.value)
    return {
        "status": result.status.value,
        "outcome": result.outcome,
        "data": to_jsonable(result.data),
        "trace_id": result.diagnostics.trace_id,
    }


# ---- N-01 ----


class InviteBody(BaseModel):
    role: Role  # 앱 토글로 고른 내 가족 역할


@app.post("/v1/pairs/invite")
async def invite(body: InviteBody, authorization: str | None = Auth):
    return service.invite(authorization, body.role)


class ClaimBody(BaseModel):
    role: Role
    invite_code: str | None = Field(None, pattern=r"^\d{6}$")
    pair_id: str | None = Field(None, max_length=128)


@app.post("/v1/pairs/claim")
async def claim(body: ClaimBody, authorization: str | None = Auth):
    """코드 입력(토큰 없음 → 새 토큰 발급) 또는 코드를 만든 쪽의 대기 확인(그때 받은 토큰)."""
    result, token = service.claim(authorization, body.role, body.invite_code, body.pair_id)
    return _reply(result) | ({"token": token} if token else {})


@app.post("/v1/pairs/unpair")
async def unpair(authorization: str | None = Auth):
    """'가족 연결 끊기'. 통화가 끝나도 페어는 유지되고, 이것으로만 끊긴다."""
    service.unpair(service.actor(authorization))
    return {"outcome": "unpaired"}


# ---- N-02 ----


class ConsentBody(BaseModel):
    call_id: str | None = Field(None, max_length=128)
    enabled: bool
    consent_revision: int = Field(ge=0)  # 마지막으로 받은 서버 버전
    device_capabilities: list[Capability] = []


@app.get("/v1/consent")
async def get_consent(authorization: str | None = Auth):
    """STALE_BASIS를 받은 앱이 현재 동의를 다시 읽는 경로."""
    return to_jsonable(service.get_consent(service.actor(authorization)))


@app.post("/v1/consent")
async def consent(body: ConsentBody, authorization: str | None = Auth):
    who = service.actor(authorization)
    caps = frozenset(body.device_capabilities)
    return _reply(service.set_consent(who, body.enabled, body.consent_revision, caps, body.call_id))


# ---- N-03 ----


@app.post("/v1/calls")
async def start_call(authorization: str | None = Auth):
    return _reply(service.call_op(Operation.START, service.actor(authorization)))


@app.post("/v1/calls/{call_id}/accept")
async def accept_call(call_id: str, authorization: str | None = Auth):
    return _reply(service.call_op(Operation.ACCEPT, service.actor(authorization), call_id))


@app.post("/v1/calls/{call_id}/end")
async def end_call(call_id: str, authorization: str | None = Auth):
    return _reply(service.call_op(Operation.END, service.actor(authorization), call_id))


@app.get("/v1/calls/current")
async def current_call(authorization: str | None = Auth):
    return service.current_call(service.actor(authorization))


@app.post("/v1/calls/{call_id}/rtc-token")
async def rtc_token(call_id: str, authorization: str | None = Auth):
    return service.rtc_token(service.actor(authorization), call_id)


@app.post("/v1/livekit/webhook")
async def livekit_webhook(request: Request, authorization: str | None = Auth):
    return service.livekit_webhook((await request.body()).decode(), authorization)


# ---- dev 전용 ----


@app.post("/v1/dev/calls/{call_id}/joined")
async def dev_joined(call_id: str, authorization: str | None = Auth):
    """worker 없이(키 없이) 테스트할 때 참가를 손으로 알린다. 평소에는 worker가 한다."""
    return _reply(service.call_op(Operation.JOINED, service.actor(authorization), call_id))


class SpeechBody(BaseModel):
    call_id: str = Field(min_length=1, max_length=128)
    speaker_role: Role
    text: str = Field(min_length=1, max_length=500)


@app.post("/v1/dev/speech-final")
async def dev_speech_final(body: SpeechBody, authorization: str | None = Auth):
    who = service.actor(authorization)
    return _reply(service.dev_speech_final(who, body.call_id, body.speaker_role, body.text))


@app.get("/v1/dev/outbox")
async def dev_outbox():
    return [{"trace_id": tid, **asdict(e)} for tid, e in OUTBOX]
