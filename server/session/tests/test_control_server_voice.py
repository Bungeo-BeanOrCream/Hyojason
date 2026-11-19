"""제어 서버 + B 음성 서비스 (#10: B는 판단 함수, D는 라우트·도움 세션 / #23: B가 call_id, D는 같은 ID로 등록).

D의 이벤트 경계는 FakeVoice로 격리하고, 기본 실행과 페어 해제·동의는 실제 B 모듈로 검증한다.
실기기나 외부 서비스에 접속하지 않으므로 결과는 FIXTURE다.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import StrEnum
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="제어 서버 테스트는 fastapi·httpx가 필요하다 (pyproject dev)")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from hyojason_session.api.agent_bridge import AgentBridge  # noqa: E402
from hyojason_session.api.app import ServerConfig, create_app, create_app_from_env, voice_from_env  # noqa: E402
from hyojason_session.api.voice import BVoiceService  # noqa: E402
from hyojason_session.common.enums import Role  # noqa: E402
from hyojason_session.common.types import ActorContext  # noqa: E402
from hyojason_session.help_state import Event  # noqa: E402

from test_control_server import CHILD_FORBIDDEN, collect, env, internal, keys, of, send, types  # noqa: E402

PARENT_H = {"Authorization": "Bearer b-parent"}
CHILD_H = {"Authorization": "Bearer b-child"}


class Operation(StrEnum):
    START = "START"
    ACCEPT = "ACCEPT"
    JOINED = "JOINED"
    END = "END"


class Capability(StrEnum):
    CAPTURE = "capture"
    OVERLAY = "overlay"


@dataclass(frozen=True)
class CallData:
    """B N-03 결과의 data처럼 call_id 속성을 가진 값."""

    call_id: str
    trace_id: str | None = None


class FakeVoice:
    """B service.py와 같은 이름의 함수만 흉내 낸 MOCK. 통화 ID·trace_id를 B처럼 여기서 만든다."""

    class ServiceError(Exception):
        def __init__(self, code):
            super().__init__(code)
            self.code = SimpleNamespace(value=code)

    def __init__(self) -> None:
        self.actors = {"b-parent": ActorContext("p1", "pair-b", Role.PARENT),
                       "b-child": ActorContext("c1", "pair-b", Role.CHILD)}
        self.consents: dict[str, SimpleNamespace] = {}
        self.calls: dict[str, dict] = {}
        self.sink = lambda trace_id, event: None
        self._ids = itertools.count(1)

    # 인증·이벤트
    def authenticate(self, token):
        return self.actors.get(token)

    def role_of(self, pair_id, user_id):
        return next((a.role for a in self.actors.values() if (a.pair_id, a.user_id) == (pair_id, user_id)), None)

    def set_event_sink(self, fn):
        self.sink = fn

    def _ok(self, outcome, **data):
        diagnostics = SimpleNamespace(trace_id=data.get("trace_id") or "trace-x", terminal_error=None)
        value = CallData(**data) if "call_id" in data else data
        return SimpleNamespace(status="success", outcome=outcome, data=value, diagnostics=diagnostics)

    def _fail(self, code):
        raise self.ServiceError(code)

    def _emit(self, kind, call, role, reason=None):
        payload = {"call_id": call["call_id"], "role": role.value if role else None, "reason": reason}
        self.sink(call["trace_id"], Event(kind, f"b-evt-{next(self._ids)}", call["pair_id"], call["call_id"], None,
                                          payload))

    # N-03
    def call_op(self, op, who, call_id=None, reason=None):
        if op is Operation.START:
            n = next(self._ids)
            call = {"call_id": f"b-call-{n}", "trace_id": f"b-trace-{n}", "pair_id": who.pair_id,
                    "caller": who.role, "joined": set(), "state": "RINGING"}
            self.calls[call["call_id"]] = call
            self._emit("call.invite", call, who.role)
            return self._ok("ringing", call_id=call["call_id"], trace_id=call["trace_id"])
        call = self.calls.get(call_id) or self._fail("STALE_BASIS")
        if op is Operation.ACCEPT:
            if who.role is call["caller"]:
                self._fail("UNAUTHORIZED")
            self._emit("call.accepted", call, who.role)
            return self._ok("accepted", call_id=call_id, trace_id=call["trace_id"])
        if op is Operation.JOINED:
            call["joined"].add(who.role)
            if len(call["joined"]) == 2 and call["state"] != "ACTIVE":
                call["state"] = "ACTIVE"
                self._emit("call.connected", call, None)
            return self._ok("active" if call["state"] == "ACTIVE" else "joined", call_id=call_id)
        call["state"] = "ENDED"
        self._emit("call.ended", call, who.role, "HANGUP")
        return self._ok("ended", call_id=call_id)

    def current_call(self, who):
        live = [c for c in self.calls.values() if c["pair_id"] == who.pair_id and c["state"] != "ENDED"]
        return {"call_id": live[-1]["call_id"], "call_state": live[-1]["state"]} if live else None

    def rtc_token(self, who, call_id):
        self._fail("DEPENDENCY_UNAVAILABLE")  # LiveKit 키 없음 (B와 같은 응답)

    def livekit_webhook(self, body, authorization):
        self._fail("DEPENDENCY_UNAVAILABLE")

    # N-01
    def invite(self, authorization, role):
        return {"pair_id": "pair-b", "invite_code": "123456"}

    def claim(self, authorization, role, invite_code=None, pair_id=None):
        return self._ok("paired", pair_id="pair-b"), (None if authorization else "new-device-token")

    def unpair(self, who):
        for call in self.calls.values():
            if call["pair_id"] == who.pair_id and call["state"] != "ENDED":
                self.call_op(Operation.END, who, call["call_id"])

    # N-02
    def get_consent(self, who):
        state = self.consents.get(who.pair_id)
        both = bool(state) and all(state.enabled.get(r) for r in Role)
        return SimpleNamespace(agent_allowed=both, consent_revision=state.revision if state else 0)

    def set_consent(self, who, enabled, consent_revision, caps, call_id):
        state = self.consents.setdefault(who.pair_id, SimpleNamespace(revision=0, enabled={}))
        if consent_revision != state.revision:
            return SimpleNamespace(status="error", outcome=None, data=None, diagnostics=SimpleNamespace(
                trace_id="t", terminal_error=SimpleNamespace(code="STALE_BASIS")))
        state.enabled[who.role] = enabled
        state.revision += 1
        return self._ok("consent_applied", consent_revision=state.revision)

    # dev
    def dev_speech_final(self, who, call_id, speaker_role, text):
        call = self.calls[call_id]
        n = next(self._ids)
        self.sink(call["trace_id"], Event("speech.final", f"b-evt-{n}", who.pair_id, call_id, None, {
            "utterance_id": f"manual-{n}", "utterance_seq": n, "speaker_role": speaker_role.value, "text": text,
            "call_id": call_id, "source": "manual_test"}))
        return self._ok("final_committed", call_id=call_id)


@pytest.fixture
def b():
    fake = FakeVoice()
    voice = BVoiceService(fake, Operation, Capability)
    app = create_app(ServerConfig(authenticator=voice, pairing=voice, service_token="svc",
                                  agent=AgentBridge.with_fixture_model(), enable_voice_dev=True))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, fake=fake, hub=app.state.hub)


def consent_both(client):
    for h in (PARENT_H, CHILD_H):
        rev = client.get("/v1/consent", headers=h).json()["consent_revision"]
        r = client.post("/v1/consent", headers=h, json={"enabled": True, "consent_revision": rev})
        assert r.status_code == 200, r.text


def test_b_issued_call_is_registered_in_d_with_the_same_ids(b):
    consent_both(b.client)
    with b.client.websocket_connect("/v1/events", headers=CHILD_H) as cws:
        r = b.client.post("/v1/calls", headers=PARENT_H)
        body = r.json()
        assert (r.status_code, body["call_id"], body["trace_id"]) == (201, "b-call-1", "b-trace-1")
        assert (body["outcome"], body["call_state"], body["help_id"].startswith("help-")) == ("ringing", "RINGING", True)
        assert types(collect(cws))[:2] == ["call.invite", "help.view"]
    control = b.hub.control_for("b-call-1")
    assert control.trace_id == "b-trace-1"
    assert b.hub._calls["b-call-1"]._session.processing_allowed  # 통화 전 B 동의가 N-11에 반영됨


def test_child_can_call_and_parent_accepts_then_b_joined_connects(b):
    r = b.client.post("/v1/calls", headers=CHILD_H)
    call = r.json()["call_id"]
    assert b.client.post(f"/v1/calls/{call}/accept", headers=CHILD_H).json() == {"detail": "UNAUTHORIZED"}
    r = b.client.post(f"/v1/calls/{call}/accept", headers=PARENT_H)
    assert (r.status_code, r.json()["call_state"]) == (200, "RINGING")  # 수락만으로는 연결이 아니다 (B N-03)
    for h in (PARENT_H, CHILD_H):
        b.client.post(f"/v1/dev/calls/{call}/joined", headers=h)
    assert b.hub.control_for(call).snapshot.call_state == "ACTIVE"
    assert b.client.get("/v1/calls/current", headers=CHILD_H).json() == {"call_id": call, "call_state": "ACTIVE"}


def test_b_speech_reaches_c_and_parent_through_d(b):
    consent_both(b.client)
    with b.client.websocket_connect("/v1/events", headers=PARENT_H) as pws, \
            b.client.websocket_connect("/v1/events", headers=CHILD_H) as cws:
        call = b.client.post("/v1/calls", headers=PARENT_H).json()["call_id"]
        b.client.post(f"/v1/calls/{call}/accept", headers=CHILD_H)
        for h in (PARENT_H, CHILD_H):
            b.client.post(f"/v1/dev/calls/{call}/joined", headers=h)
        help_id = b.hub.control_for(call).snapshot.help_id
        send(pws, env("projection.started", call, help_id, {"projection_epoch": "cap-1"}, pair="pair-b"))
        send(pws, env("screen.observed", call, help_id, {
            "projection_epoch": "cap-1", "screen_revision": 1, "observation_health": "ACTIVE",
            "screen_kind": "settings_home", "target_ids": ["settings.display"], "evidence_codes": []}, pair="pair-b"))
        collect(pws), collect(cws)

        r = b.client.post("/v1/dev/speech-final", headers=CHILD_H, json={
            "call_id": call, "speaker_role": "CHILD", "text": "디스플레이 눌러 줘"})
        assert r.json()["outcome"] == "final_committed"
        parent, child = collect(pws), collect(cws)
    assert of(parent, "target.located")[0]["payload"]["target_id"] == "settings.display"
    assert types(parent).index("instruction.accepted") < types(parent).index("target.located")
    assert not keys([m.get("payload", {}) for m in child]) & CHILD_FORBIDDEN


def test_consent_goes_through_b_and_revocation_ends_the_help(b):
    consent_both(b.client)
    with b.client.websocket_connect("/v1/events", headers=CHILD_H) as cws:
        call = b.client.post("/v1/calls", headers=PARENT_H).json()["call_id"]
        help_id = b.hub.control_for(call).snapshot.help_id
        collect(cws)
        # B 모드에서는 WS consent.updated를 받지 않는다 (B N-02가 맡음)
        reply = send(cws, env("consent.updated", call, help_id, {"purpose": "analysis", "enabled": False},
                              pair="pair-b"))
        assert of(reply, "event.rejected")[0]["code"] == "FORBIDDEN"
        rev = b.client.get("/v1/consent", headers=CHILD_H).json()["consent_revision"]
        r = b.client.post("/v1/consent", headers=CHILD_H, json={"enabled": False, "consent_revision": rev})
        assert r.json()["outcome"] == "consent_applied"
        ended = of(collect(cws), "help.ended")
    assert [m["payload"]["reason"] for m in ended] == ["CONSENT_REVOKED"]
    stale = b.client.post("/v1/consent", headers=CHILD_H, json={"enabled": True, "consent_revision": 0})
    assert (stale.status_code, stale.json()) == (409, {"detail": "STALE_BASIS"})  # B 앱이 쓰던 오류 모양


def test_end_call_and_b_routes(b):
    call = b.client.post("/v1/calls", headers=PARENT_H).json()["call_id"]
    r = b.client.post(f"/v1/calls/{call}/end", headers=CHILD_H)
    assert (r.json()["outcome"], r.json()["call_state"]) == ("ended", "ENDED")
    assert b.client.get("/v1/calls/current", headers=PARENT_H).json() is None
    assert b.client.post("/v1/pairs/invite", json={"role": "PARENT"}).json()["invite_code"] == "123456"
    claimed = b.client.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": "123456"}).json()
    assert (claimed["outcome"], claimed["token"]) == ("paired", "new-device-token")
    assert b.client.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": "12"}).status_code == 422
    assert b.client.post(f"/v1/calls/{call}/rtc-token", headers=PARENT_H).status_code == 503
    assert b.client.post("/v1/pairs/unpair", headers=PARENT_H).json() == {"outcome": "unpaired"}


def test_register_call_is_idempotent_only_for_the_same_pair_and_trace(b):
    first = b.hub.register_call("pair-b", "b-call-x", "trace-x", Role.PARENT)
    assert b.hub.register_call("pair-b", "b-call-x", "trace-x", Role.PARENT) is first
    for pair, trace in (("pair-b", "trace-other"), ("pair-other", "trace-x")):
        with pytest.raises(Exception, match="CONFLICT"):
            b.hub.register_call(pair, "b-call-x", trace, Role.PARENT)
    # 모르는 통화의 B 이벤트는 B 처리를 막지 않고 무시한다
    assert b.hub.on_voice_event("t", Event("call.accepted", "e", "pair-b", "nope", None, {"role": "CHILD"})) is None


def test_mock_mode_has_no_b_routes():
    from hyojason_session.mocks import MockTokenAuthenticator
    app = create_app(ServerConfig(authenticator=MockTokenAuthenticator({}), service_token="svc"))
    with TestClient(app) as client:
        assert client.post("/v1/pairs/invite", json={"role": "PARENT"}).status_code == 404
        assert client.get("/v1/consent").status_code == 404
    assert voice_from_env("mock") is None
    with pytest.raises(RuntimeError):
        voice_from_env("other")


@pytest.fixture
def real_b(monkeypatch):
    pytest.importorskip("livekit.api", reason="실제 B 검증에는 server/voice/requirements.txt 필요")
    from hyojason_voice import service, worker

    for key in (*worker.REQUIRED_ENV, "HYOJASON_VOICE", "HYOJASON_ENABLE_VOICE_DEV", "HYOJASON_MOCK_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HYOJASON_SERVICE_TOKEN", "service-token")
    monkeypatch.setenv("HYOJASON_AGENT", "off")
    for name in ("pairs", "calls", "tokens", "consents", "finalizers", "workers"):
        monkeypatch.setattr(service, name, type(getattr(service, name))())
    monkeypatch.setattr(service, "_sink", service._sink)
    return service


def real_pair(client):
    invite = client.post("/v1/pairs/invite", json={"role": "PARENT"})
    assert invite.status_code == 200, invite.text
    claim = client.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": invite.json()["invite_code"]})
    assert claim.json()["outcome"] == "paired", claim.text
    return ({"Authorization": f"Bearer {invite.json()['token']}"},
            {"Authorization": f"Bearer {claim.json()['token']}"}, invite.json()["pair_id"])


def test_b01_1_default_uses_real_b_without_dev_injections(real_b):
    app = create_app_from_env()
    with TestClient(app) as client:
        parent, child, _ = real_pair(client)
        call = client.post("/v1/calls", headers=child).json()
        cid = call["call_id"]
        assert call["trace_id"] == real_b.calls.get(cid).trace_id
        assert client.post(f"/v1/calls/{cid}/accept", headers=parent).json()["call_state"] == "RINGING"
        assert client.post(f"/v1/calls/{cid}/rtc-token", headers=child).json() == {"detail": "DEPENDENCY_UNAVAILABLE"}
        assert client.post(f"/v1/dev/calls/{cid}/joined", headers=parent).status_code == 404
        assert client.post("/v1/dev/speech-final", headers=child).status_code == 404
        assert client.post(f"/v1/calls/{cid}/end", headers=child).status_code == 200
        assert client.get("/v1/consent", headers=parent).status_code == 200


@pytest.mark.parametrize("termination", ["help-stop", "post-call-unpair", "post-call-revoke"])
def test_b07_real_b_unpair_closes_roles_and_ends_help(real_b, monkeypatch, termination):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("HYOJASON_ENABLE_VOICE_DEV", "1")
    app = create_app_from_env()
    with TestClient(app) as client:
        parent, child, pair_id = real_pair(client)
        for headers in (parent, child):
            revision = client.get("/v1/consent", headers=headers).json()["consent_revision"]
            assert client.post("/v1/consent", headers=headers,
                               json={"enabled": True, "consent_revision": revision}).status_code == 200
        with client.websocket_connect("/v1/events", headers=parent) as pws, \
                client.websocket_connect("/v1/events", headers=child) as cws:
            call = client.post("/v1/calls", headers=parent).json()
            cid = call["call_id"]
            assert client.post(f"/v1/calls/{cid}/accept", headers=child).status_code == 200
            for headers in (parent, child):
                assert client.post(f"/v1/dev/calls/{cid}/joined", headers=headers).status_code == 200
            assert real_b.calls.get(cid).state == "ACTIVE"
            control = app.state.hub.control_for(cid)
            if termination != "help-stop":
                assert internal(client, cid, "speech.final", {
                    "speaker_role": "CHILD", "utterance_id": "t1", "utterance_seq": 1,
                    "text": "fixture-only utterance"}).status_code == 200
                assert internal(client, cid, "goal.drafted", {
                    "source_utterance_id": "t1", "goal_id": "SET_TIMEOUT",
                    "approved_slots": {"duration_minutes": 10}}).status_code == 200
                assert client.post(f"/v1/calls/{cid}/end", headers=child).status_code == 200
                assert (control.snapshot.call_state, control.snapshot.mode) == ("ENDED", "POST_CALL_AGENT")
                if termination == "post-call-revoke":
                    revision = client.get("/v1/consent", headers=child).json()["consent_revision"]
                    assert client.post("/v1/consent", headers=child,
                                       json={"enabled": False, "consent_revision": revision}).status_code == 200
                    assert control.snapshot.help_state == "ENDED"
            else:
                reply = send(pws, env("help.stop", cid, call["help_id"], {}, pair=pair_id))
                assert of(reply, "event.ack")
                assert client.get("/v1/consent", headers=child).json()["agent_allowed"] is False
                assert real_b.calls.get(cid).state == "ACTIVE"
                late = client.post("/v1/dev/speech-final", headers=child, json={
                    "call_id": cid, "speaker_role": "CHILD", "text": "디스플레이 눌러 줘"})
                assert late.json() == {"detail": "NO_CONSENT"}
            collect(pws), collect(cws)
            assert client.post("/v1/pairs/unpair", headers=child).status_code == 200
            assert control.snapshot.help_state == "ENDED"
            for ws in (pws, cws):
                with pytest.raises(WebSocketDisconnect) as closed:
                    while True:
                        ws.receive_json()
                assert closed.value.code == 4401
        for headers in (parent, child):
            stale = client.get("/v1/calls/current", headers=headers)
            assert (stale.status_code, stale.json()) == (403, {"detail": "UNAUTHORIZED"})
