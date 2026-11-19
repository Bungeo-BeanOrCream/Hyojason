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


def _pair(c, inviter="PARENT"):
    """초대·코드 입력으로 페어를 만들고 각자 받은 Bearer 토큰 헤더를 돌려준다. (P, C, pair_id)"""
    other = "CHILD" if inviter == "PARENT" else "PARENT"
    inv = c.post("/v1/pairs/invite", json={"role": inviter}).json()
    code = next(k for k, v in m.pairs.invites.items() if v.pair_id == inv["pair_id"])
    r = c.post("/v1/pairs/claim", json={"role": other, "invite_code": code}).json()
    assert r["outcome"] == "paired"
    h = {inviter: {"Authorization": f"Bearer {inv['token']}"}, other: {"Authorization": f"Bearer {r['token']}"}}
    return h["PARENT"], h["CHILD"], inv["pair_id"]


def _active_call(c):
    P, C, pid = _pair(c)
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
    r = c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": "12ab"})
    assert r.status_code == 422
    assert c.post("/v1/calls", headers={"Authorization": "Bearer nope"}).status_code == 403
    r = c.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": "123456"})  # 없는 코드
    assert r.status_code == 403 and "token" not in r.json()  # 실패하면 토큰을 주지 않는다


def test_rtc_token_only_for_accepted_call_microphone_only(monkeypatch):
    from livekit import api as lk

    c = _client()
    P, C, pid = _pair(c, "PARENT")
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

    assert c.post(f"/v1/calls/{cid}/rtc-token", headers={"Authorization": "Bearer nope"}).status_code == 403
    c.post(f"/v1/calls/{cid}/end", headers=P)
    assert c.post(f"/v1/calls/{cid}/rtc-token", headers=C).status_code == 409  # 종료 후


def test_worker_hooks_drive_call_and_speech_final():
    """worker가 본 참가·퇴장·확정문이 N-03/N-06을 거쳐 이벤트가 된다 (LiveKit 없이 콜백만)."""
    c = _client()
    P, C, pid = _pair(c, "PARENT")
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


def test_current_call_for_polling():
    c = _client()
    P, C, pid = _pair(c, "CHILD")
    assert c.get("/v1/calls/current", headers=P).json() is None
    cid = c.post("/v1/calls", headers=C).json()["data"]["call_id"]  # 자녀 발신
    cur = c.get("/v1/calls/current", headers=P).json()
    assert cur == {"call_id": cid, "call_state": "RINGING", "caller_role": "CHILD", "accepted": False, "connected_roles": []}
    c.post(f"/v1/calls/{cid}/accept", headers=P)
    assert c.get("/v1/calls/current", headers=C).json()["accepted"] is True
    c.post(f"/v1/calls/{cid}/end", headers=P)
    assert c.get("/v1/calls/current", headers=C).json() is None
    assert c.get("/v1/calls/current", headers={"Authorization": "Bearer nope"}).status_code == 403


def test_b01_bearer_token_auth():
    """#10 합의안: invite/claim 때 받은 토큰만 믿는다. 헤더로 actor·pair를 바꿔 행세할 수 없다."""
    c = _client()
    inv = c.post("/v1/pairs/invite", json={"role": "PARENT"}).json()
    P = {"Authorization": f"Bearer {inv['token']}"}
    assert c.post("/v1/calls", headers=P).status_code == 403  # 아직 대기 중인 페어는 통화 불가
    wait = {"role": "PARENT", "pair_id": inv["pair_id"]}
    assert c.post("/v1/pairs/claim", json=wait).status_code == 403  # 대기 확인은 만든 쪽 토큰으로만
    assert c.post("/v1/pairs/claim", json=wait, headers=P).json()["outcome"] == "pending"

    code = next(k for k, v in m.pairs.invites.items() if v.pair_id == inv["pair_id"])
    C = {"Authorization": f"Bearer {c.post('/v1/pairs/claim', json={'role': 'CHILD', 'invite_code': code}).json()['token']}"}
    assert c.post("/v1/pairs/claim", json=wait, headers=P).json()["outcome"] == "paired"
    assert "token" not in c.post("/v1/pairs/claim", json=wait, headers=P).json()  # 기존 기기에는 새로 주지 않음
    cid = c.post("/v1/calls", headers=P).json()["data"]["call_id"]

    P2, _, _ = _pair(c)  # 다른 가족
    assert c.post(f"/v1/calls/{cid}/accept", headers=P2).status_code in (403, 409)
    assert c.post(f"/v1/calls/{cid}/accept", headers={"Authorization": inv["token"]}).status_code == 403  # Bearer 없음
    assert c.post(f"/v1/calls/{cid}/accept", headers=C).json()["outcome"] == "waiting"


def test_b01_livekit_webhook_joined_left(monkeypatch):
    """#5: 서명된 LiveKit 웹훅의 참가·퇴장이 N-03 JOINED/END가 된다. 위조·남의 페어는 무시·거부."""
    import base64, hashlib, json

    from livekit import api as lk

    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "devsecret-devsecret-devsecret-32b")

    def hook(event, room, ident, secret="devsecret-devsecret-devsecret-32b"):
        body = json.dumps({"event": event, "room": {"name": room}, "participant": {"identity": ident}})
        sha = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        jwt = lk.AccessToken("devkey", secret).with_sha256(sha).to_jwt()
        return c.post("/v1/livekit/webhook", content=body, headers={"Authorization": jwt, "Content-Type": "application/webhook+json"})

    c = _client()
    P, C, pid = _pair(c)
    cid = c.post("/v1/calls", headers=P).json()["data"]["call_id"]
    c.post(f"/v1/calls/{cid}/accept", headers=C)

    assert hook("participant_joined", cid, f"{pid}:PARENT", secret="wrong-secret-wrong-secret-wrong-32").status_code == 403
    assert hook("participant_joined", cid, "stt-worker").json() == {"ignored": "unknown_participant"}
    assert hook("participant_joined", cid, "pair-other:CHILD").json() == {"ignored": "unknown_participant"}
    assert hook("participant_joined", cid, f"{pid}:PARENT").json()["outcome"] == "waiting"
    assert hook("participant_joined", cid, f"{pid}:CHILD").json()["outcome"] == "active"
    assert hook("participant_joined", cid, f"{pid}:CHILD").json()["outcome"] == "active"  # worker와 중복돼도 그대로
    hook("participant_left", cid, f"{pid}:CHILD")
    hook("participant_left", cid, f"{pid}:PARENT")  # 이미 끝난 통화: 이벤트 추가 없음

    ev = [(e["type"], e["payload"].get("reason")) for e in c.get("/v1/dev/outbox").json()]
    assert ev == [("call.invite", None), ("call.accepted", None), ("call.connected", None), ("call.ended", "RTC_FAILED")]


def test_b01_pair_survives_call_end_until_unpair():
    """통화가 끝나도 가족 연결은 유지된다. '가족 연결 끊기'(unpair)로만 끊기고, 그때 진행 중 통화도 끝난다."""
    c = _client()
    P, C, cid = _active_call(c)
    c.post(f"/v1/calls/{cid}/end", headers=P)
    assert c.get("/v1/consent", headers=C).status_code == 200  # 통화 끝나도 페어 유지
    cid2 = c.post("/v1/calls", headers=C).json()["data"]["call_id"]  # 같은 페어로 다시 통화

    assert c.post("/v1/pairs/unpair", headers=C).json() == {"outcome": "unpaired"}
    assert c.get("/v1/consent", headers=P).status_code == 403  # 상대 기기도 끊김 → 앱은 연결 화면으로
    assert c.post("/v1/pairs/unpair", headers=P).status_code == 403
    assert m.calls.get(cid2).end_reason.value == "HANGUP"
    assert [e["type"] for e in c.get("/v1/dev/outbox").json()][-1] == "call.ended"
    assert c.post("/v1/pairs/invite", json={"role": "PARENT"}).status_code == 200  # 새 코드로 다시 연결 가능


def test_service_is_framework_free_for_d():
    """#10: D가 자기 라우트에서 부를 함수들. 인증은 D의 MockTokenAuthenticator와 같은 모양, 이벤트는 sink로."""
    from hyojason_voice import service as s

    c = _client()
    got = []
    s.set_event_sink(lambda tid, ev: got.append((tid, ev.type)))
    try:
        inv = s.invite(None, Role.PARENT)
        code = next(k for k, v in s.pairs.invites.items() if v.pair_id == inv["pair_id"])
        result, ctok = s.claim(None, Role.CHILD, code)
        assert result.outcome == "paired" and ctok
        parent = s.authenticate(inv["token"])  # D는 'Bearer ' 뺀 토큰을 넘긴다
        assert (parent.pair_id, parent.role) == (inv["pair_id"], Role.PARENT)
        assert s.authenticate("nope") is None and s.authenticate(None) is None
        assert s.role_of(inv["pair_id"], parent.user_id) is Role.PARENT
        r = s.call_op(m.Operation.START, parent)
        assert got == [(r.diagnostics.trace_id, "call.invite")]
    finally:
        s.set_event_sink(lambda tid, ev: s.OUTBOX.append((tid, ev)))
