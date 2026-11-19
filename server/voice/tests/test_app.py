"""B01.1 · B01.2 · B01.4 · B02.3 흐름을 HTTP로 확인 (FIXTURE, LiveKit 없음)."""

import pytest
from fastapi.testclient import TestClient

from hyojason_session.common.enums import Role

from hyojason_voice import app as m
from hyojason_voice import worker


@pytest.fixture(autouse=True)
def _no_live_keys(monkeypatch):
    """셸에 .env가 올라가 있어도 테스트에서 실제 LiveKit·RTZR에 붙지 않게 한다."""
    for k in worker.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)


def _client():
    m.pairs.__init__()
    m.calls.__init__()
    m.consents.clear()
    m.finalizers.clear()
    m.workers.clear()
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


def test_rtc_token_only_for_accepted_call_microphone_only(monkeypatch):
    from livekit import api as lk

    c = _client()
    pid = c.post("/v1/pairs/invite", json={"role": "PARENT"}, headers={"X-Actor-Id": "P"}).json()["pair_id"]
    code = next(k for k, v in m.pairs.invites.items() if v.pair_id == pid)
    c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": code}, headers={"X-Actor-Id": "C"})
    P, C = {"X-Actor-Id": "P", "X-Pair-Id": pid}, {"X-Actor-Id": "C", "X-Pair-Id": pid}
    cid = c.post("/v1/calls", headers=P).json()["data"]["call_id"]

    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert c.post(f"/v1/calls/{cid}/rtc-token", headers=P).status_code == 403  # 수락 전
    c.post(f"/v1/calls/{cid}/accept", headers=C)
    assert c.post(f"/v1/calls/{cid}/rtc-token", headers=P).status_code == 503  # 키 없음

    monkeypatch.setenv("LIVEKIT_URL", "wss://example.invalid")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret-devsecret-devsecret-0000")
    r = c.post(f"/v1/calls/{cid}/rtc-token", headers=C).json()
    claims = lk.TokenVerifier("devkey", "devsecret-devsecret-devsecret-0000").verify(r["token"])
    assert claims.identity == f"{pid}:CHILD" and r["identity"] == claims.identity
    assert claims.video.room == cid and claims.video.room_join
    assert claims.video.can_publish_sources == ["microphone"] and not claims.video.can_publish_data

    assert c.post(f"/v1/calls/{cid}/rtc-token", headers={"X-Actor-Id": "Z", "X-Pair-Id": pid}).status_code == 403
    c.post(f"/v1/calls/{cid}/end", headers=P)
    assert c.post(f"/v1/calls/{cid}/rtc-token", headers=C).status_code == 409  # 종료 후


def test_worker_hooks_drive_call_and_speech_final():
    """worker가 본 참가·퇴장·확정문이 N-03/N-06을 거쳐 이벤트가 된다 (LiveKit 없이 콜백만)."""
    c = _client()
    pid = c.post("/v1/pairs/invite", json={"role": "PARENT"}, headers={"X-Actor-Id": "P"}).json()["pair_id"]
    code = next(k for k, v in m.pairs.invites.items() if v.pair_id == pid)
    c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": code}, headers={"X-Actor-Id": "C"})
    P, C = {"X-Actor-Id": "P", "X-Pair-Id": pid}, {"X-Actor-Id": "C", "X-Pair-Id": pid}
    cid = c.post("/v1/calls", headers=P).json()["data"]["call_id"]
    c.post(f"/v1/calls/{cid}/accept", headers=C)
    assert cid not in m.workers

    h = m._hooks(cid)
    h.final(Role.CHILD, "CHILD-0", 0, "너무 이름", 0.0, 0)  # ACTIVE 전 → 버림
    h.joined(Role.PARENT)
    h.joined(Role.CHILD)
    _set_consent(c, C, True)
    _set_consent(c, P, True, ["MIC"])
    rev = h.consent().consent_revision
    h.final(Role.CHILD, "CHILD-1", 1, "디스플레이 눌러 줘", 0.0, rev)
    h.final(Role.CHILD, "CHILD-1", 1, "디스플레이 눌러 줘", 0.0, rev)  # 중복
    h.final(Role.CHILD, "CHILD-2", 2, "옛 동의", 0.0, rev - 1)  # 발화 중 동의 바뀜 → 버림
    h.left(Role.CHILD)
    h.final(Role.CHILD, "CHILD-3", 3, "끊긴 뒤", 0.0, rev)  # 종료 후 → 버림 (B02.3)

    out = c.get("/v1/dev/outbox").json()
    assert [e["type"] for e in out] == ["call.invite", "call.accepted", "call.connected", "speech.final", "call.ended"]
    sf = out[3]["payload"]
    assert sf["source"] == "stt" and sf["speaker_role"] == "CHILD" and sf["utterance_seq"] == 1
    assert out[4]["payload"]["reason"] == "RTC_FAILED"


def test_call_end_deletes_livekit_room(monkeypatch):
    """종료되면 방을 지워 남은 기기도 끊긴다. LiveKit 호출은 가짜로 바꿔 요청만 확인."""
    deleted = []

    class FakeAPI:
        def __init__(self, url):
            assert url.startswith("https://")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        @property
        def room(self):
            return self

        async def delete_room(self, req):
            deleted.append(req.room)

    c = _client()
    P, C, cid = _active_call(c)
    monkeypatch.setattr(m.lk, "LiveKitAPI", FakeAPI)
    for k, v in (("LIVEKIT_URL", "wss://x.livekit.cloud"), ("LIVEKIT_API_KEY", "k"), ("LIVEKIT_API_SECRET", "s")):
        monkeypatch.setenv(k, v)
    c.post(f"/v1/calls/{cid}/end", headers=P)
    c.post(f"/v1/calls/{cid}/end", headers=P)  # 중복 종료는 다시 지우지 않음
    c.get("/v1/dev/outbox")  # 이벤트 루프가 한 번 더 돌게
    assert deleted == [cid]
