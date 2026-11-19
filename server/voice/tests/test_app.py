"""B01.1 · B01.2 · B01.4 · B02.3 흐름을 HTTP로 확인 (FIXTURE, LiveKit 없음)."""

from fastapi.testclient import TestClient

from hyojason_voice import app as m


def _client():
    m.pairs.__init__()
    m.calls.__init__()
    m.consents.clear()
    m.finalizers.clear()
    m.OUTBOX.clear()
    return TestClient(m.app)


def _active_call(c):
    pid = c.post("/v1/pairs/invite", json={"role": "PARENT"}, headers={"X-Actor-Id": "P"}).json()["pair_id"]
    code = next(k for k, v in m.pairs.invites.items() if v.pair_id == pid)
    r = c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": code}, headers={"X-Actor-Id": "C"})
    assert r.json()["outcome"] == "paired"
    P, C = {"X-Actor-Id": "P", "X-Pair-Id": pid}, {"X-Actor-Id": "C", "X-Pair-Id": pid}
    cid = c.post("/v1/calls", headers=P).json()["data"]["call_id"]
    c.post(f"/v1/calls/{cid}/accept", headers=C)
    c.post(f"/v1/dev/calls/{cid}/joined", headers=P)
    assert c.post(f"/v1/dev/calls/{cid}/joined", headers=C).json()["outcome"] == "active"
    return P, C, cid


def _set_consent(c, headers, enabled, caps=()):
    rev = c.get("/v1/consent", headers=headers).json()["consent_revision"]
    body = {"enabled": enabled, "consent_revision": rev, "device_capabilities": list(caps)}
    return c.post("/v1/consent", json=body, headers=headers)


def test_b01_1_call_flow_and_b02_3_late_result():
    c = _client()
    P, C, cid = _active_call(c)
    _set_consent(c, C, True)
    _set_consent(c, P, True, ["MIC"])
    body = {"call_id": cid, "speaker_role": "CHILD", "text": "디스플레이 눌러 줘"}
    assert c.post("/v1/dev/speech-final", json=body, headers=P).json()["outcome"] == "final_committed"
    c.post(f"/v1/calls/{cid}/end", headers=C)
    c.post(f"/v1/calls/{cid}/end", headers=C)  # 중복 종료는 이벤트를 다시 내지 않음
    assert c.post("/v1/dev/speech-final", json=body, headers=P).status_code == 409  # 종료 후 폐기

    types = [e["type"] for e in c.get("/v1/dev/outbox").json()]
    assert types == ["call.invite", "call.accepted", "call.connected", "speech.final", "call.ended"]
    assert {e["trace_id"] for e in c.get("/v1/dev/outbox").json()} == {m.calls.get(cid).trace_id}  # #5


def test_b01_2_voice_only_without_consent_then_revoke():
    c = _client()
    P, C, cid = _active_call(c)
    body = {"call_id": cid, "speaker_role": "CHILD", "text": "화면 꺼지는 거"}
    assert c.post("/v1/dev/speech-final", json=body, headers=P).json()["detail"] == "NO_CONSENT"

    _set_consent(c, C, True)
    _set_consent(c, P, True, ["MIC"])
    assert c.post("/v1/dev/speech-final", json=body, headers=P).json()["outcome"] == "final_committed"

    _set_consent(c, C, False)  # 자녀 철회: STT만 막히고 통화는 유지
    assert c.post("/v1/dev/speech-final", json=body, headers=P).status_code == 409
    assert m.calls.get(cid).state.value == "ACTIVE"


def test_stale_consent_revision_is_rejected():
    c = _client()
    P, C, _ = _active_call(c)
    _set_consent(c, C, True)  # 버전 0 → 1
    r = c.post("/v1/consent", json={"enabled": True, "consent_revision": 0}, headers=P)
    assert r.status_code == 409 and r.json()["detail"] == "STALE_BASIS"


def test_rejects_bad_input_and_strangers():
    c = _client()
    r = c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": "12ab"}, headers={"X-Actor-Id": "Z"})
    assert r.status_code == 422
    assert c.post("/v1/calls", headers={"X-Actor-Id": "Z", "X-Pair-Id": "nope"}).status_code == 403
    assert c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": "123456"}).status_code == 422  # 헤더 없음
