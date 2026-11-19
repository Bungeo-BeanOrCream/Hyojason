"""#6 준비 · 제어 서버: 지원 대상을 자녀로 연 통화와 새 코드 승인으로 여는 다음 도움.

REST·WS 테스트 클라이언트로 확인한다. MOCK: 인증(B-01), 페어·통화(N-01~N-03). 결과는 FIXTURE.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="제어 서버 테스트는 fastapi·httpx가 필요하다 (pyproject dev)")
pytest.importorskip("httpx")


from test_control_server import (  # noqa: E402
    CHILD_FORBIDDEN, CHILD_H, PARENT_H, SERVICE, collect, env, internal, keys, of, send, types,
)
from hyojason_session.api.wire import COUNTERPART_DELIVERABLE  # noqa: E402


def test_server_child_assisted_call_then_new_link_back_to_parent(client):
    with client.websocket_connect("/v1/events", headers=PARENT_H) as pws, \
            client.websocket_connect("/v1/events", headers=CHILD_H) as cws:
        r = client.post("/v1/calls", headers=PARENT_H, json={"assisted_role": "CHILD"})
        assert (r.status_code, r.json()["assisted_role"]) == (201, "CHILD")
        call, help_id, trace = r.json()["call_id"], r.json()["help_id"], r.json()["trace_id"]
        collect(cws)
        client.post(f"/v1/calls/{call}/accept", headers=CHILD_H)
        for ws in (pws, cws):
            send(ws, env("consent.updated", call, help_id, {"purpose": "analysis", "enabled": True}))
        # 관찰은 지원 대상(자녀) 기기만
        assert of(send(pws, env("projection.started", call, help_id, {"projection_epoch": "c1"})),
                  "event.rejected")[0]["code"] == "FORBIDDEN"
        assert of(send(cws, env("projection.started", call, help_id, {"projection_epoch": "c1"})), "event.ack")
        send(cws, env("screen.observed", call, help_id, {
            "projection_epoch": "c1", "screen_revision": 1, "observation_health": "ACTIVE",
            "screen_kind": "settings_home", "target_ids": ["settings.display"], "evidence_codes": []}))
        collect(pws)
        # 지시는 부모가 말한다 → 안내는 자녀 기기로
        internal(client, call, "speech.final", {"speaker_role": "PARENT", "utterance_id": "t1",
                                                "utterance_seq": 1, "text": "fixture"})
        internal(client, call, "instruction.drafted", {"source_utterance_id": "t1", "action": "TAP",
                                                       "target_key": "settings.display", "supersedes_previous": True})
        child, parent = collect(cws), collect(pws)
        assert of(child, "instruction.accepted")
        assert of(child, "help.view")[-1]["payload"]["parent_view"]["status"] == "CALL_GUIDING"
        assert set(types(parent)) <= COUNTERPART_DELIVERABLE  # 부모(상대)에게는 안내·지시 없음
        assert not keys([m.get("payload", {}) for m in parent]) & CHILD_FORBIDDEN
        # 완료는 부모(상대)가 누른다
        done = send(pws, env("help.complete", call, help_id))
        assert of(done, "event.ack")[0]["outcome"] == "end_requested"
        assert [m["payload"]["reason"] for m in of(collect(cws), "help.ended")] == ["PARENT_COMPLETED"]
        # 같은 통화에서 새 코드 승인 → 새 도움, 새 trace, 이번엔 부모 지원
        assert internal(client, call, "help.link_approved", {"assisted_role": "PARENT"}).status_code == 200
        now = client.get("/v1/help/current", headers=PARENT_H).json()
        assert now["data"]["snapshot"]["help_id"] != help_id
        assert now["trace_id"] != trace
        assert now["data"]["snapshot"]["parent_view"]["status"] == "LISTENING"
        # 새 도움은 캡처 미승인(NOT_GRANTED)부터 시작한다. 지원 대상이 캡처를 새로 시작해야 하므로
        # 재관찰 요청 대상이 아니다 (#6 A 의견: 새 도움 = 새 projection_epoch).
        assert now["data"]["must_reobserve"] is False


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from hyojason_session.api.app import ServerConfig, create_app
    from hyojason_session.mocks import MockTokenAuthenticator
    from test_control_server import TOKENS
    app = create_app(ServerConfig(authenticator=MockTokenAuthenticator(TOKENS), service_token="service-token"))
    with TestClient(app) as c:
        yield c
