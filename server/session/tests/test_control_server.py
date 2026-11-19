"""FastAPI 제어 서버 — REST·WS 전체 흐름 (결정 메모 6·7, 이슈 #10).

실제 HTTP/WebSocket 테스트 클라이언트로 부모·자녀 앱과 서버 내부 생산자(B-02·C·N-03)를 흉내 낸다.
MOCK: 인증 토큰(B-01), 페어·통화·RTC(N-01~N-03). 결과는 모두 FIXTURE.
"""

from __future__ import annotations

import itertools

import pytest

pytest.importorskip("fastapi", reason="제어 서버 테스트는 fastapi·httpx가 필요하다 (pyproject dev)")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from hyojason_session.api.app import ServerConfig, create_app, create_app_from_env  # noqa: E402
from hyojason_session.api.wire import COUNTERPART_DELIVERABLE  # noqa: E402
from hyojason_session.common.enums import Role  # noqa: E402
from hyojason_session.common.types import ActorContext  # noqa: E402
from hyojason_session.mocks import MockTokenAuthenticator  # noqa: E402

TOKENS = {
    "parent-token": ActorContext("parent", "pair", Role.PARENT),
    "child-token": ActorContext("child", "pair", Role.CHILD),
    "other-token": ActorContext("other", "pair-other", Role.PARENT),
}
SERVICE = {"X-Service-Token": "service-token"}
PARENT_H = {"Authorization": "Bearer parent-token"}
CHILD_H = {"Authorization": "Bearer child-token"}
OTHER_H = {"Authorization": "Bearer other-token"}

# 자녀에게 나가면 안 되는 키 (목표·지시·화면·버전·이력)
CHILD_FORBIDDEN = {
    "goal", "goal_id", "goal_revision", "approved_slots", "instruction", "instruction_id",
    "target_key", "screen", "screen_kind", "target_ids", "evidence_codes", "basis", "mode",
    "status", "projection_epoch", "screen_revision", "trail_cursor",
}

_ids = itertools.count(1)


def keys(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys(v) for v in value)) if value else set()
    return set()


def env(kind, call, help_id, payload=None, *, pair="pair", event_id=None):
    return {"v": 1, "type": kind, "event_id": event_id or f"e{next(_ids)}", "pair_id": pair,
            "call_id": call, "help_id": help_id, "payload": payload or {}}


def collect(ws) -> list[dict]:
    """ping을 보내고 pong이 올 때까지 쌓인 메시지를 모두 받는다 (큐는 순서를 지킨다)."""
    tag = f"ping{next(_ids)}"
    ws.send_json({"type": "ping", "event_id": tag})
    out = []
    while True:
        message = ws.receive_json()
        if message.get("type") == "pong" and message.get("event_id") == tag:
            return out
        out.append(message)


def send(ws, message) -> list[dict]:
    ws.send_json(message)
    return collect(ws)


def types(messages) -> list[str]:
    return [m["type"] for m in messages]


def of(messages, kind) -> list[dict]:
    return [m for m in messages if m["type"] == kind]


@pytest.fixture
def client():
    app = create_app(ServerConfig(authenticator=MockTokenAuthenticator(TOKENS), service_token="service-token"))
    with TestClient(app) as c:
        yield c


def internal(client, call, kind, payload=None, **extra):
    body = {"type": kind, "payload": payload or {}, **extra}
    return client.post(f"/v1/internal/calls/{call}/events", json=body, headers=SERVICE)


class Call:
    """부모 발신 → 자녀 수락 → 양쪽 동의 → 캡처 시작 → 설정 홈 관찰까지 진행한 통화."""

    def __init__(self, client, pws, cws):
        self.client, self.pws, self.cws = client, pws, cws
        r = client.post("/v1/calls", headers=PARENT_H)
        assert r.status_code == 201, r.text
        self.call, self.help, self.trace = r.json()["call_id"], r.json()["help_id"], r.json()["trace_id"]
        self.invite = collect(cws)
        assert client.post(f"/v1/calls/{self.call}/accept", headers=CHILD_H).status_code == 200
        for ws in (pws, cws):
            replies = send(ws, env("consent.updated", self.call, self.help,
                                   {"purpose": "analysis", "enabled": True}))
            assert of(replies, "event.ack"), replies
        send(pws, env("projection.started", self.call, self.help, {"projection_epoch": "capture-1"}))
        self.observe()
        collect(cws)

    def observe(self, revision=1, evidence=()):
        return send(self.pws, env("screen.observed", self.call, self.help, {
            "projection_epoch": "capture-1", "screen_revision": revision, "observation_health": "ACTIVE",
            "screen_kind": "settings_home", "target_ids": ["settings.display"],
            "evidence_codes": list(evidence)}))

    def child_turn(self, seq=1, goal=True):
        assert internal(self.client, self.call, "speech.final", {
            "speaker_role": "CHILD", "utterance_id": f"t{seq}", "utterance_seq": seq,
            "text": "fixture-only utterance"}).status_code == 200
        r = internal(self.client, self.call, "instruction.drafted", {
            "source_utterance_id": f"t{seq}", "action": "TAP", "target_key": "settings.display",
            "supersedes_previous": True})
        assert r.status_code == 200, r.text
        if goal:
            r = internal(self.client, self.call, "goal.drafted", {
                "source_utterance_id": f"t{seq}", "goal_id": "SET_TIMEOUT",
                "approved_slots": {"duration_minutes": 10}})
            assert r.status_code == 200, r.text


@pytest.fixture
def call(client):
    with client.websocket_connect("/v1/events", headers=PARENT_H) as pws, \
            client.websocket_connect("/v1/events", headers=CHILD_H) as cws:
        yield Call(client, pws, cws)


# --- 통화 시작 ------------------------------------------------------------------


def test_call_creation_issues_trace_and_invites_child(call):
    assert call.trace.startswith("trace-")
    assert types(call.invite) == ["call.invite", "help.view"]  # 수신 알림 먼저, 그다음 자녀 화면
    assert (call.invite[0]["call_id"], call.invite[0]["payload"]) == (call.call, {})
    r = call.client.get("/v1/help/current", params={"help_id": call.help}, headers=PARENT_H)
    body = r.json()
    assert (r.status_code, body["outcome"], body["trace_id"]) == (200, "snapshot_ready", call.trace)
    assert body["data"]["must_reobserve"] is True


def test_child_instruction_reaches_parent_only_and_views_are_role_scoped(call):
    call.child_turn()
    parent, child = collect(call.pws), collect(call.cws)
    [accepted] = of(parent, "instruction.accepted")
    assert accepted["payload"]["target_key"] == "settings.display"
    assert accepted["payload"]["basis"]["instruction_revision"] == 1
    assert of(parent, "goal.updated")[0]["payload"]["goal_id"] == "SET_TIMEOUT"
    parent_view = of(parent, "help.view")[-1]["payload"]["parent_view"]
    assert (parent_view["status"], parent_view["goal"]["goal_id"]) == ("CALL_GUIDING", "SET_TIMEOUT")
    # 자녀: 지시·목표 이벤트 없음, 화면은 세 키뿐
    assert set(types(child)) <= COUNTERPART_DELIVERABLE
    assert of(child, "help.view")[-1]["payload"]["child_view"] == {
        "call_state": "ACTIVE", "can_complete": True, "arrival_indicator": "NONE"}
    assert not keys([m.get("payload", {}) for m in child]) & CHILD_FORBIDDEN


def test_post_call_arrival_and_child_completion_end_once(call):
    call.child_turn()
    collect(call.pws), collect(call.cws)
    assert internal(call.client, call.call, "call.ended").status_code == 200
    parent = collect(call.pws)
    assert {"mode.changed", "screen.reobserve"} <= set(types(parent))
    call.observe()  # 통화 후 새 관찰
    collect(call.cws)

    r = call.client.post(f"/v1/internal/calls/{call.call}/arrivals", headers=SERVICE, json={
        "arrival_id": "arr-1", "arrived": True, "evidence_codes": ["timeout_10m_selected"]})
    assert (r.status_code, r.json()["outcome"], r.json()["announcement"]) == (
        200, "arrival_announced", "SUSPECTED")
    child = collect(call.cws)
    assert of(child, "arrival.suspected")[0]["payload"] == {"arrival_id": "arr-1"}
    assert of(child, "help.view")[-1]["payload"]["child_view"]["arrival_indicator"] == "SUSPECTED"

    done = send(call.cws, env("help.complete", call.call, call.help))
    again = send(call.cws, env("help.complete", call.call, call.help))
    parent = collect(call.pws)
    assert of(done, "event.ack")[0]["outcome"] == "end_requested"
    assert of(again, "event.ack")[0]["outcome"] == "duplicate_ignored"
    assert [m["payload"]["reason"] for m in of(done + again, "help.ended")] == ["CHILD_COMPLETED"]
    assert [m["payload"]["reason"] for m in of(parent, "help.ended")] == ["CHILD_COMPLETED"]
    final = of(parent, "help.view")[-1]["payload"]["parent_view"]
    assert (final["status"], final["completion_message_key"]) == ("COMPLETED", "help.completed")
    assert not keys([m.get("payload", {}) for m in child + done + again]) & CHILD_FORBIDDEN

    r = call.client.get("/v1/help/current", params={"help_id": call.help}, headers=CHILD_H)
    assert (r.json()["outcome"], r.json()["data"]["resume_state"]) == ("no_active_help", "ENDED")


def test_parent_stop_ends_help_and_blocks_new_turn_until_reconsent(call):
    call.child_turn(goal=False)
    collect(call.pws), collect(call.cws)
    replies = send(call.pws, env("help.stop", call.call, call.help))
    assert of(replies, "event.ack")[0]["outcome"] == "end_requested"
    assert [m["payload"]["reason"] for m in of(collect(call.cws), "help.ended")] == ["PARENT_STOPPED"]
    r = internal(call.client, call.call, "speech.final", {
        "speaker_role": "CHILD", "utterance_id": "t2", "utterance_seq": 2, "text": "x"})
    assert (r.status_code, r.json()["error"]["node_code"]) == (409, "NO_CONSENT")


# --- 거절 경로 ------------------------------------------------------------------


def test_rest_auth_and_role_errors(client):
    assert client.post("/v1/calls").status_code == 401
    assert client.post("/v1/calls", headers={"Authorization": "Bearer nope"}).status_code == 401
    call_id = client.post("/v1/calls", headers=PARENT_H).json()["call_id"]
    assert client.post("/v1/calls", headers=PARENT_H).json()["error"] == {
        "code": "CONFLICT", "node_code": "CALL_ALREADY_OPEN"}
    assert client.post("/v1/calls", headers=CHILD_H).json()["error"]["node_code"] == "CALL_ALREADY_OPEN"
    # 건 쪽은 자기 통화를 수락할 수 없다 (발신은 양쪽, 수락은 상대만 — #6·#10)
    assert client.post(f"/v1/calls/{call_id}/accept", headers=PARENT_H).json()["error"] == {
        "code": "FORBIDDEN", "node_code": "UNAUTHORIZED"}
    assert client.post(f"/v1/calls/{call_id}/accept", headers=OTHER_H).status_code == 403
    assert client.post("/v1/calls/call-missing/accept", headers=CHILD_H).status_code == 404
    rtc = client.post(f"/v1/calls/{call_id}/rtc-token", headers=CHILD_H).json()
    assert (rtc["rtc_token"], rtc["source"]) == (None, "FIXTURE")  # MOCK N-03
    assert client.post("/v1/pairs/claim", headers=CHILD_H).json()["approved"] is True
    r = client.get("/v1/help/current", headers=OTHER_H)
    assert (r.json()["outcome"], r.json()["data"]["resume_state"]) == ("no_active_help", "NO_HELP")


def test_internal_routes_require_service_token_and_block_end_paths(call):
    assert call.client.post(f"/v1/internal/calls/{call.call}/events", json={"type": "call.ended"}).status_code == 401
    for kind in ("help.end", "help.complete", "arrival.suspected"):
        r = internal(call.client, call.call, kind, {"reason": "CHILD_COMPLETED", "arrival_id": "a"})
        assert r.json()["error"]["code"] == "FORBIDDEN", kind
    # 승인되지 않은 슬롯 값(실제 장소 이름)은 받지 않는다
    internal(call.client, call.call, "speech.final", {
        "speaker_role": "CHILD", "utterance_id": "t9", "utterance_seq": 9, "text": "x"})
    r = internal(call.client, call.call, "goal.drafted", {
        "source_utterance_id": "t9", "goal_id": "MAP_ROUTE", "approved_slots": {"place": "서울역"}})
    assert r.json()["error"]["code"] == "INVALID_EVENT"


def test_ws_rejections_keep_connection_open(call):
    rejected = lambda messages: of(messages, "event.rejected")[0]  # noqa: E731
    r = rejected(send(call.pws, env("screen.observed", call.call, call.help, pair="pair-other")))
    assert (r["code"], r["node_code"]) == ("FORBIDDEN", "UNAUTHORIZED")
    r = rejected(send(call.cws, env("screen.observed", call.call, call.help)))  # 자녀는 화면을 못 보냄
    assert r["code"] == "FORBIDDEN"
    bad = env("consent.updated", call.call, call.help)
    bad["extra"] = 1
    assert rejected(send(call.pws, bad))["code"] == "INVALID_EVENT"
    call.pws.send_text("not json")
    assert rejected(collect(call.pws))["code"] == "INVALID_EVENT"
    r = rejected(send(call.cws, env("help.complete", call.call, "help-old")))
    assert (r["code"], r["node_code"]) == ("CONFLICT", "STALE_BASIS")
    assert of(send(call.pws, env("guide.suppressed", "call-missing", call.help)), "event.rejected")[0][
        "code"] == "NOT_FOUND"
    # 연결은 그대로 쓸 수 있다
    assert collect(call.pws) == []


def test_ws_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/events", headers={"Authorization": "Bearer nope"}) as ws:
            ws.receive_json()


def test_app_from_env_requires_settings(monkeypatch):
    monkeypatch.delenv("HYOJASON_MOCK_TOKENS", raising=False)
    monkeypatch.delenv("HYOJASON_SERVICE_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app_from_env()
    monkeypatch.setenv("HYOJASON_MOCK_TOKENS", "p=parent:pair:PARENT;c=child:pair:CHILD")
    monkeypatch.setenv("HYOJASON_SERVICE_TOKEN", "svc")
    with TestClient(create_app_from_env()) as c:
        assert c.get("/healthz").json() == {"ok": True, "source": "FIXTURE"}
