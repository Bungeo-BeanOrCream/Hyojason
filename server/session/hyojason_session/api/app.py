"""FastAPI 제어 서버 — docs/implementation-contract.md '제어 엔드포인트 제안'.

기본 인증·페어·동의·통화는 B의 판단 함수(voice.BVoiceService, #10)가 맡는다.
HYOJASON_VOICE=mock은 고정 토큰과 D가 발급하는 통화를 사용하는 개발용 모드다.

| 경로 | 호출자 | 처리 |
|---|---|---|
| POST /v1/pairs/invite · /claim · /unpair | 두 역할 | B N-01 (mock: claim만, 페어 그대로 승인) |
| GET·POST /v1/consent | 두 역할 | B N-02 → 진행 중 통화의 N-11 동의 동기화 (B 모드 전용) |
| POST /v1/calls | 두 역할 | 발신(body: assisted_role, 기본 PARENT). B 모드는 B가 call_id·trace_id를 만들고 D가 같은 ID로 등록 |
| POST /v1/calls/{call_id}/accept · /end | 건 쪽의 상대 · 참가자 | 수락·종료. mock은 수락하면 곧바로 call.connected |
| GET /v1/calls/current | 페어 참가자 | 진행 중 통화 (B 앱 폴링·복구) |
| POST /v1/calls/{call_id}/rtc-token | 페어 참가자 | B: LiveKit 토큰 / mock: null |
| POST /v1/livekit/webhook | LiveKit | B 모드의 참가·퇴장 |
| POST /v1/dev/... | 개발 | B 모드에서 HYOJASON_ENABLE_VOICE_DEV=1일 때만 모의 참가·확정 발화 허용 |
| GET /v1/help/current | 페어 참가자 | N-13 재접속 조회 |
| WS /v1/events | 페어 참가자 | 앱 이벤트 수신, N-11 효과·help.ended·help.view 송신 |
| POST /v1/internal/calls/{call_id}/events | 서버 내부(B-02·C·N-03) | 서버 이벤트 적용 |
| POST /v1/internal/calls/{call_id}/arrivals | 서버 내부(C의 N-18) | 도착 추정 → N-22 |

인증: 앱은 `Authorization: Bearer <token>`(B 기기 토큰 또는 MOCK), 내부 경로는 `X-Service-Token`.
실행: `uvicorn hyojason_session.api.app:create_app_from_env --factory` (server/session/.env.example 참고)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from ..common.enums import Role, Source
from ..common.result import new_id
from ..common.serialize import to_jsonable
from ..common.types import ActorContext
from ..mocks.auth import MockTokenAuthenticator
from ..mocks.pairing import MockPairingService
from ..navigation import NavigationCatalog
from . import wire
from .catalog import APPROVED_SLOTS, default_catalog
from .hub import SessionHub
from .voice import VoiceError
from .wire import WireError


@dataclass
class ServerConfig:
    # MOCK: MockTokenAuthenticator·MockPairingService. B 연결: 둘 다 voice.BVoiceService (#10)
    authenticator: Any
    service_token: str
    catalog: NavigationCatalog = field(default_factory=default_catalog)
    approved_slots: Mapping[str, Callable] = field(default_factory=lambda: dict(APPROVED_SLOTS))
    pairing: Any = field(default_factory=MockPairingService)
    # 외부 실행 증거가 확인되기 전의 기본 출처다 (AGENTS.md).
    source: Source = Source.FIXTURE
    # B-05 연결(agent_bridge.AgentBridge). None이면 C 판단 없이 동작한다.
    agent: Any = None
    enable_voice_dev: bool = False


def _bearer(value: str | None) -> str | None:
    if value and value.startswith("Bearer "):
        return value[len("Bearer "):].strip() or None
    return None


def _error(api_code: str, node_code: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=wire.HTTP_STATUS[api_code],
                        content={"error": {"code": api_code, "node_code": node_code}})


def _node_response(result, *, extra: Mapping | None = None) -> JSONResponse:
    if result.status == "error":
        code = result.diagnostics.terminal_error.code
        return _error(wire.api_code_for(code), code)
    body = {"status": result.status, "outcome": result.outcome}
    body.update(extra or {})
    return JSONResponse(content=body)


def create_app(config: ServerConfig) -> FastAPI:
    hub = SessionHub(catalog=config.catalog, approved_slots=config.approved_slots,
                     pairs=config.authenticator, pairing=config.pairing, source=config.source,
                     agent=config.agent)
    app = FastAPI(title="Hyojason control server", version="0.1.0")
    app.state.hub = hub

    if hasattr(config.pairing, "attach"):
        config.pairing.attach(hub)  # B service.set_event_sink → hub.on_voice_event

    @app.exception_handler(WireError)
    async def _wire_error(request: Request, error: WireError):
        return _error(error.api_code, error.node_code)

    @app.exception_handler(VoiceError)
    async def _voice_error(request: Request, error: VoiceError):
        # B 경로의 오류는 B 앱이 쓰던 모양 그대로 (server/voice/hyojason_voice/app.py)
        return JSONResponse(status_code=error.http_status, content={"detail": error.code})

    def voice():
        if not hub.voice_backed:
            raise WireError("NOT_FOUND")  # MOCK 모드에는 B 경로가 없다
        return hub.voice

    def voice_actor(authorization: str | None) -> ActorContext:
        actor = config.authenticator.authenticate(_bearer(authorization))
        if actor is None:
            raise VoiceError("UNAUTHORIZED")
        return actor

    def call_body(control, result=None) -> dict:
        s = control.snapshot
        body = {"call_id": s.call_id, "help_id": s.help_id, "trace_id": control.trace_id,
                "call_state": s.call_state, "assisted_role": s.assisted_role}
        return _b_reply(result) | body if result is not None else body

    def actor_of(authorization: str | None) -> ActorContext:
        actor = config.authenticator.authenticate(_bearer(authorization))
        if actor is None:
            raise WireError("UNAUTHENTICATED")
        return actor

    call_actor = voice_actor if hub.voice_backed else actor_of

    def service(token: str | None) -> None:
        if not token or token != config.service_token:
            raise WireError("UNAUTHENTICATED")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "source": config.source.value}

    # --- N-01 페어 (B) -----------------------------------------------------------
    @app.post("/v1/pairs/claim")
    async def claim(request: Request, authorization: str | None = Header(default=None)):
        if not hub.voice_backed:
            return hub.claim(actor_of(authorization))  # MOCK N-01
        body = await _json_body(request)
        role, code, pair_id = _role(body.get("role")), body.get("invite_code"), body.get("pair_id")
        if (set(body) - {"role", "invite_code", "pair_id"}
                or (code is not None and not (isinstance(code, str) and re.fullmatch(r"\d{6}", code)))
                or (pair_id is not None and not (isinstance(pair_id, str) and len(pair_id) <= 128))):
            raise VoiceError("INVALID_INPUT")
        result, token = voice().claim(authorization, role, code, pair_id)
        return _b_reply(result) | ({"token": token} if token else {})

    @app.post("/v1/pairs/invite")
    async def invite(request: Request, authorization: str | None = Header(default=None)):
        body = await _json_body(request)
        return voice().invite(authorization, _role(body.get("role")))

    @app.post("/v1/pairs/unpair")
    async def unpair(authorization: str | None = Header(default=None)):
        service = voice()
        actor = voice_actor(authorization)
        service.unpair(actor)
        hub.on_unpair(actor)
        return {"outcome": "unpaired"}

    # --- N-02 동의 (B) → N-11 -------------------------------------------------------
    @app.get("/v1/consent")
    async def get_consent(authorization: str | None = Header(default=None)):
        return to_jsonable(voice().get_consent(voice_actor(authorization)))

    @app.post("/v1/consent")
    async def set_consent(request: Request, authorization: str | None = Header(default=None)):
        service = voice()
        body = await _json_body(request)
        actor = voice_actor(authorization)
        caps, revision = body.get("device_capabilities", []), body.get("consent_revision")
        if (set(body) - {"call_id", "enabled", "consent_revision", "device_capabilities"}
                or type(body.get("enabled")) is not bool or type(revision) is not int or revision < 0
                or not isinstance(caps, list) or not all(isinstance(c, str) for c in caps)):
            raise VoiceError("INVALID_INPUT")
        try:
            result = service.set_consent(actor, body["enabled"], revision, caps, body.get("call_id"))
        except ValueError:  # 모르는 기기 능력
            raise VoiceError("INVALID_INPUT") from None
        hub.sync_consent(actor.pair_id)  # 진행 중 통화의 N-11 동의를 맞춘다 (철회면 도움 종료)
        return _b_reply(result)

    # --- N-03 통화 ----------------------------------------------------------------
    @app.post("/v1/calls", status_code=201)
    async def create_call(request: Request, authorization: str | None = Header(default=None)):
        body = await _json_body(request) if await request.body() else {}
        actor = call_actor(authorization)
        if set(body) - {"assisted_role"} or body.get("assisted_role", "PARENT") not in ("PARENT", "CHILD"):
            raise WireError("INVALID_EVENT")
        control, result = hub.start_call(actor, Role(body.get("assisted_role", "PARENT")))
        return call_body(control, result)

    @app.post("/v1/calls/{call_id}/accept")
    async def accept_call(call_id: str, authorization: str | None = Header(default=None)):
        control, result = hub.accept(call_actor(authorization), call_id)
        return call_body(control, result)

    @app.post("/v1/calls/{call_id}/end")
    async def end_call(call_id: str, authorization: str | None = Header(default=None)):
        control, result = hub.end_call(call_actor(authorization), call_id)
        return call_body(control, result)

    @app.get("/v1/calls/current")
    async def current_call(authorization: str | None = Header(default=None)):
        return hub.current_call(call_actor(authorization))

    @app.post("/v1/calls/{call_id}/rtc-token")
    async def rtc_token(call_id: str, authorization: str | None = Header(default=None)):
        return hub.rtc_token(call_actor(authorization), call_id)

    @app.post("/v1/livekit/webhook")
    async def livekit_webhook(request: Request, authorization: str | None = Header(default=None)):
        return voice().livekit_webhook((await request.body()).decode(), authorization)

    # --- dev (B): STT·LiveKit 키 없이 통합할 때 ------------------------------------------
    def voice_dev():
        if not config.enable_voice_dev:
            raise WireError("NOT_FOUND")
        return voice()

    @app.post("/v1/dev/calls/{call_id}/joined")
    async def dev_joined(call_id: str, authorization: str | None = Header(default=None)):
        return _b_reply(voice_dev().joined(voice_actor(authorization), call_id))

    @app.post("/v1/dev/speech-final")
    async def dev_speech_final(request: Request, authorization: str | None = Header(default=None)):
        service = voice_dev()
        body = await _json_body(request)
        actor = voice_actor(authorization)
        call_id, text = body.get("call_id"), body.get("text")
        if (not isinstance(call_id, str) or not 0 < len(call_id) <= 128
                or not isinstance(text, str) or not 0 < len(text) <= 500):
            raise VoiceError("INVALID_INPUT")
        return _b_reply(service.dev_speech_final(actor, call_id, _role(body.get("speaker_role")), text))

    @app.get("/v1/help/current")
    async def help_current(help_id: str | None = None, last_event_seq: int = 0,
                           snapshot_request_id: str | None = None,
                           authorization: str | None = Header(default=None)):
        result = hub.reconnect(actor_of(authorization), help_id, last_event_seq,
                               snapshot_request_id or new_id("snap"))
        if result.status == "error":
            code = result.diagnostics.terminal_error.code
            return _error(wire.api_code_for(code), code)
        return {"status": result.status, "outcome": result.outcome, "data": to_jsonable(result.data),
                "trace_id": result.diagnostics.trace_id}

    @app.post("/v1/internal/calls/{call_id}/events")
    async def internal_event(call_id: str, request: Request,
                             x_service_token: str | None = Header(default=None)):
        service(x_service_token)
        body = await _json_body(request)
        return _node_response(hub.apply_server_event(call_id, body))

    @app.post("/v1/internal/calls/{call_id}/arrivals")
    async def internal_arrival(call_id: str, request: Request,
                               x_service_token: str | None = Header(default=None)):
        service(x_service_token)
        body = await _json_body(request)
        result = hub.submit_arrival(call_id, body)
        extra = {"announcement": to_jsonable(result.data.announcement)} if result.data else None
        return _node_response(result, extra=extra)

    @app.websocket("/v1/events")
    async def events(ws: WebSocket):
        actor = config.authenticator.authenticate(_bearer(ws.headers.get("authorization")))
        if actor is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        if config.authenticator.role_of(actor.pair_id, actor.user_id) != actor.role:
            await ws.close(code=4401)
            return
        queue: asyncio.Queue = asyncio.Queue()
        conn = hub.connect(actor, queue, asyncio.get_running_loop())

        async def pump():
            while True:
                message = await queue.get()
                if message is None:
                    await ws.close(code=4401)
                    return
                await ws.send_json(message)

        sender = asyncio.create_task(pump())
        try:
            while True:
                text = await ws.receive_text()
                if not hub.is_current_connection(conn):
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    queue.put_nowait(wire.rejected(None, "INVALID_EVENT"))
                    continue
                queue.put_nowait(hub.handle_client(actor, message))
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(conn)
            sender.cancel()

    return app


def _role(value) -> Role:
    if value not in ("PARENT", "CHILD"):
        raise VoiceError("INVALID_INPUT")
    return Role(value)


def _b_reply(result) -> dict:
    """B app.py의 _reply와 같은 응답 모양."""
    return {"status": to_jsonable(result.status), "outcome": result.outcome, "data": to_jsonable(result.data),
            "trace_id": result.diagnostics.trace_id}


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise WireError("INVALID_EVENT") from None
    if not isinstance(body, dict):
        raise WireError("INVALID_EVENT")
    return body


def agent_from_env(mode: str | None):
    """HYOJASON_AGENT: off | fixture(MOCK Zoro-1) | zoro(ZORO_DECIDE_URL 등, server/agent/README.md).

    C 코드(server.agent)를 import하므로 저장소 루트가 PYTHONPATH에 있어야 한다.
    모델 호출이 WS 이벤트 루프를 막지 않게 작업 스레드에서 돌린다.
    """
    mode = (mode or "").strip().lower()
    if not mode:
        configured = os.environ.get("ZORO_DECIDE_URL") or os.environ.get("ZORO_MODEL_REVISION")
        mode = "zoro" if configured else "off"
    if mode == "off":
        return None
    if mode not in ("fixture", "zoro"):
        raise RuntimeError("HYOJASON_AGENT must be off, fixture or zoro")
    try:
        from .agent_bridge import AgentBridge
    except ModuleNotFoundError as error:
        raise RuntimeError("HYOJASON_AGENT needs the repository root on PYTHONPATH (server.agent)") from error
    if mode == "fixture":
        return AgentBridge.with_fixture_model(threaded=True)
    return AgentBridge.with_zoro_from_env(threaded=True)


def voice_from_env(mode: str | None):
    """HYOJASON_VOICE: service(기본, B의 hyojason_voice.service) | mock(고정 토큰·D 발급 통화).

    service는 server/voice를 PYTHONPATH에 두고 B의 requirements(livekit-api 등)를 설치해야 한다.
    """
    mode = (mode or "service").strip().lower()
    if mode == "mock":
        return None
    if mode != "service":
        raise RuntimeError("HYOJASON_VOICE must be mock or service")
    from .voice import BVoiceService
    try:
        return BVoiceService.from_import()
    except ModuleNotFoundError as error:
        raise RuntimeError("HYOJASON_VOICE=service needs server/voice on PYTHONPATH and its requirements") from error


def create_app_from_env() -> FastAPI:
    """B의 서비스로 기본 실행한다. 환경 설정은 .env.example을 참고한다."""
    service_token = os.environ.get("HYOJASON_SERVICE_TOKEN")
    if not service_token:
        raise RuntimeError("set HYOJASON_SERVICE_TOKEN (see server/session/.env.example)")
    agent = agent_from_env(os.environ.get("HYOJASON_AGENT"))
    voice = voice_from_env(os.environ.get("HYOJASON_VOICE"))
    if voice is not None:
        return create_app(ServerConfig(authenticator=voice, pairing=voice, service_token=service_token,
                                       agent=agent, enable_voice_dev=os.environ.get("HYOJASON_ENABLE_VOICE_DEV") == "1"))
    tokens = os.environ.get("HYOJASON_MOCK_TOKENS")
    if not tokens:
        raise RuntimeError("set HYOJASON_MOCK_TOKENS for HYOJASON_VOICE=mock (see server/session/.env.example)")
    return create_app(ServerConfig(authenticator=MockTokenAuthenticator.from_spec(tokens),
                                   service_token=service_token, agent=agent))
