"""FastAPI 제어 서버 — docs/implementation-contract.md '제어 엔드포인트 제안'.

| 경로 | 호출자 | 처리 |
|---|---|---|
| POST /v1/pairs/claim | 두 역할 | MOCK N-01 페어 승인 |
| POST /v1/calls | 부모 | 통화 생성(body: assisted_role, 기본 PARENT), trace_id 발급, 자녀에게 call.invite |
| POST /v1/calls/{call_id}/accept | 자녀 | 수락. MOCK N-03이 곧바로 call.connected |
| POST /v1/calls/{call_id}/rtc-token | 페어 참가자 | MOCK: 토큰 없음(null) |
| GET /v1/help/current | 페어 참가자 | N-13 재접속 조회 |
| WS /v1/events | 페어 참가자 | 앱 이벤트 수신, N-11 효과·help.ended·help.view 송신 |
| POST /v1/internal/calls/{call_id}/events | 서버 내부(B-02·C·N-03) | 서버 이벤트 적용 |
| POST /v1/internal/calls/{call_id}/arrivals | 서버 내부(C의 N-18) | 도착 추정 → N-22 |

인증: 앱은 `Authorization: Bearer <token>`(MOCK B-01), 내부 경로는 `X-Service-Token`.
실행: `uvicorn hyojason_session.api.app:create_app_from_env --factory` (server/session/.env.example 참고)
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

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
from .wire import WireError


@dataclass
class ServerConfig:
    authenticator: MockTokenAuthenticator
    service_token: str
    catalog: NavigationCatalog = field(default_factory=default_catalog)
    approved_slots: Mapping[str, Callable] = field(default_factory=lambda: dict(APPROVED_SLOTS))
    pairing: MockPairingService = field(default_factory=MockPairingService)
    # B의 인증·페어·통화와 실제 기기가 붙기 전까지 결과는 FIXTURE다 (AGENTS.md)
    source: Source = Source.FIXTURE


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
                     pairs=config.authenticator, pairing=config.pairing, source=config.source)
    app = FastAPI(title="Hyojason control server", version="0.1.0")
    app.state.hub = hub

    @app.exception_handler(WireError)
    async def _wire_error(request: Request, error: WireError):
        return _error(error.api_code, error.node_code)

    def actor_of(authorization: str | None) -> ActorContext:
        actor = config.authenticator.authenticate(_bearer(authorization))
        if actor is None:
            raise WireError("UNAUTHENTICATED")
        return actor

    def service(token: str | None) -> None:
        if not token or token != config.service_token:
            raise WireError("UNAUTHENTICATED")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "source": config.source.value}

    @app.post("/v1/pairs/claim")
    async def claim(authorization: str | None = Header(default=None)):
        return hub.claim(actor_of(authorization))

    @app.post("/v1/calls", status_code=201)
    async def create_call(request: Request, authorization: str | None = Header(default=None)):
        actor = actor_of(authorization)
        body = await _json_body(request) if await request.body() else {}
        if set(body) - {"assisted_role"} or body.get("assisted_role", "PARENT") not in ("PARENT", "CHILD"):
            raise WireError("INVALID_EVENT")
        control = hub.create_call(actor, Role(body.get("assisted_role", "PARENT")))
        s = control.snapshot
        return {"call_id": s.call_id, "help_id": s.help_id, "trace_id": control.trace_id,
                "call_state": s.call_state, "assisted_role": s.assisted_role}

    @app.post("/v1/calls/{call_id}/accept")
    async def accept_call(call_id: str, authorization: str | None = Header(default=None)):
        control = hub.accept_call(actor_of(authorization), call_id)
        s = control.snapshot
        return {"call_id": s.call_id, "help_id": s.help_id, "trace_id": control.trace_id,
                "call_state": s.call_state}

    @app.post("/v1/calls/{call_id}/rtc-token")
    async def rtc_token(call_id: str, authorization: str | None = Header(default=None)):
        return hub.rtc_token(actor_of(authorization), call_id)

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
        queue: asyncio.Queue = asyncio.Queue()
        conn = hub.connect(actor, queue, asyncio.get_running_loop())

        async def pump():
            while True:
                await ws.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                text = await ws.receive_text()
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


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise WireError("INVALID_EVENT") from None
    if not isinstance(body, dict):
        raise WireError("INVALID_EVENT")
    return body


def create_app_from_env() -> FastAPI:
    """HYOJASON_MOCK_TOKENS, HYOJASON_SERVICE_TOKEN 환경 변수로 띄운다 (가짜 값만, .env.example)."""
    tokens = os.environ.get("HYOJASON_MOCK_TOKENS")
    service_token = os.environ.get("HYOJASON_SERVICE_TOKEN")
    if not tokens or not service_token:
        raise RuntimeError("set HYOJASON_MOCK_TOKENS and HYOJASON_SERVICE_TOKEN (see server/session/.env.example)")
    return create_app(ServerConfig(authenticator=MockTokenAuthenticator.from_spec(tokens),
                                   service_token=service_token))
