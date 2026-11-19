"""제어 서버 + B-05 연결 — 내부 경로로 넣은 발화가 C 판단을 거쳐 부모 WS에 도착한다 (#14 다음 계획 3).

B-02가 준비되기 전 흐름: POST /v1/internal/calls/{id}/events 로 speech.final 주입 → N-11 → C B05Workflow
→ N-11(instruction.drafted·goal.drafted·target.located) → 부모 WS. MOCK: 인증·페어·RTC, Zoro-1. 결과는 FIXTURE.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="제어 서버 테스트는 fastapi·httpx가 필요하다 (pyproject dev)")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from hyojason_session.api.agent_bridge import RESULT_EVENTS, AgentBridge  # noqa: E402
from hyojason_session.api.app import ServerConfig, agent_from_env, create_app  # noqa: E402
from hyojason_session.mocks import MockTokenAuthenticator  # noqa: E402

from test_control_server import (  # noqa: E402
    CHILD_FORBIDDEN, CHILD_H, PARENT_H, SERVICE, TOKENS, collect, env, internal, keys, of, send, types,
)


@pytest.fixture
def server():
    agent = AgentBridge.with_fixture_model()
    app = create_app(ServerConfig(authenticator=MockTokenAuthenticator(TOKENS), service_token="service-token",
                                  agent=agent))
    with TestClient(app) as client:
        yield client, agent


def test_injected_child_speech_reaches_parent_as_instruction_goal_and_location(server):
    client, agent = server
    with client.websocket_connect("/v1/events", headers=PARENT_H) as pws, \
            client.websocket_connect("/v1/events", headers=CHILD_H) as cws:
        r = client.post("/v1/calls", headers=PARENT_H)
        call, help_id = r.json()["call_id"], r.json()["help_id"]
        collect(cws)
        assert client.post(f"/v1/calls/{call}/accept", headers=CHILD_H).status_code == 200
        for ws in (pws, cws):
            send(ws, env("consent.updated", call, help_id, {"purpose": "analysis", "enabled": True}))
        send(pws, env("projection.started", call, help_id, {"projection_epoch": "capture-1"}))
        send(pws, env("screen.observed", call, help_id, {
            "projection_epoch": "capture-1", "screen_revision": 1, "observation_health": "ACTIVE",
            "screen_kind": "settings_home", "target_ids": ["settings.display"], "evidence_codes": []}))
        collect(pws), collect(cws)

        r = internal(client, call, "speech.final", {
            "speaker_role": "CHILD", "utterance_id": "t1", "utterance_seq": 1, "source": "FIXTURE",
            "text": "화면 자동 꺼짐을 10분으로 바꿔 줘. 디스플레이 눌러 줘"})
        assert (r.status_code, r.json()["outcome"]) == (200, "state_applied")
        parent, child = collect(pws), collect(cws)

    kinds = types(parent)
    assert kinds.index("instruction.accepted") < kinds.index("target.located")
    assert of(parent, "target.located")[0]["payload"]["target_id"] == "settings.display"
    assert of(parent, "goal.updated")[0]["payload"]["goal_id"] == "SET_TIMEOUT"
    assert not set(types(child)) & RESULT_EVENTS
    assert not keys([m.get("payload", {}) for m in child]) & CHILD_FORBIDDEN
    assert [run.event for run in agent.results.of(help_id)][-2:] == ["speech.final", "instruction.accepted"]


def test_agent_mode_from_environment():
    assert agent_from_env(None) is None and agent_from_env("off") is None
    fixture = agent_from_env("fixture")
    assert isinstance(fixture, AgentBridge)
    fixture.close()
    with pytest.raises(RuntimeError):
        agent_from_env("gpt")


def test_server_without_agent_keeps_c_out_of_the_loop():
    app = create_app(ServerConfig(authenticator=MockTokenAuthenticator(TOKENS), service_token="service-token"))
    assert app.state.hub._agent is None
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True, "source": "FIXTURE"}
        assert client.post("/v1/internal/calls/none/events", json={"type": "speech.final"},
                           headers=SERVICE).status_code == 404
