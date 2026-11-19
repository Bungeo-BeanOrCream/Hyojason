"""N-01~N-06 최소 검증 표(케이스 ID를 테스트 이름에 사용). 모두 FIXTURE, 실기기 PASS 아님."""

from hyojason_session.common.enums import CallState, Role, Source
from hyojason_session.common.result import Status
from hyojason_session.common.types import ActorContext

from hyojason_voice.audio import AudioChunk, AudioIngest, SourceKind
from hyojason_voice.call import CallRegistry, Operation, start_or_close_call
from hyojason_voice.consent import Capability, ConsentView, ConsentState, ConsentUpdate, Purpose, resolve_consent
from hyojason_voice.finalize import FinalizeRequest, TurnFinalizer
from hyojason_voice.pair import PairRegistry, PairRequest, resolve_pair
from hyojason_voice.stt import Finality, SttEvent, SttKind, TurnRequest, transcribe_korean_turn

T = dict(trace_id="t", source=Source.FIXTURE)


def _err(r):
    assert r.status is Status.ERROR and r.outcome is None and r.data is None
    return r.diagnostics.terminal_error.code


# ---- N-01 ----


def _paired():
    reg = PairRegistry()
    pid, code = reg.issue_invite("P", Role.PARENT)
    return reg, pid, code


def test_01_1_paired_and_01_2_pending():
    reg, pid, code = _paired()
    assert resolve_pair(PairRequest("P", Role.PARENT, pair_id=pid), reg, **T).outcome == "pending"
    assert reg.role_of(pid, "P") is None  # pending이면 방 금지
    r = resolve_pair(PairRequest("C", Role.CHILD, invite_code=code), reg, **T)
    assert r.outcome == "paired" and r.diagnostics.basis.pair_id == pid
    assert reg.role_of(pid, "C") is Role.CHILD


def test_01_3_reuse_expired_third_party():
    reg, pid, code = _paired()
    resolve_pair(PairRequest("C", Role.CHILD, invite_code=code), reg, **T)
    assert _err(resolve_pair(PairRequest("X", Role.CHILD, invite_code=code), reg, **T)) == "UNAUTHORIZED"
    assert _err(resolve_pair(PairRequest("X", Role.CHILD, pair_id=pid), reg, **T)) == "UNAUTHORIZED"
    reg.clock = lambda: 1e12
    _, code2 = reg.issue_invite("P2", Role.PARENT)
    reg.clock = lambda: 1e12 + 601
    assert _err(resolve_pair(PairRequest("C2", Role.CHILD, invite_code=code2), reg, **T)) == "UNAUTHORIZED"


def test_01_one_to_one_and_brute_force():
    reg, pid, code = _paired()
    resolve_pair(PairRequest("C", Role.CHILD, invite_code=code), reg, **T)
    assert reg.issue_invite("P", Role.PARENT) is None  # 확정된 부모는 새 페어 불가
    _, code2 = reg.issue_invite("P2", Role.PARENT)
    assert _err(resolve_pair(PairRequest("C", Role.CHILD, invite_code=code2), reg, **T)) == "UNAUTHORIZED"
    for _ in range(5):
        resolve_pair(PairRequest("X", Role.CHILD, invite_code="000000"), reg, **T)
    assert _err(resolve_pair(PairRequest("X", Role.CHILD, invite_code=code2), reg, **T)) == "UNAUTHORIZED"


def test_01_reissue_invalidates_old_code():
    reg, pid, old = _paired()
    pid2, new = reg.issue_invite("P", Role.PARENT)
    assert pid2 == pid and new != old
    assert _err(resolve_pair(PairRequest("C", Role.CHILD, invite_code=old), reg, **T)) == "UNAUTHORIZED"
    assert resolve_pair(PairRequest("C", Role.CHILD, invite_code=new), reg, **T).outcome == "paired"


def test_01_toggle_role_child_invites_parent_claims():
    reg = PairRegistry()
    pid, code = reg.issue_invite("C", Role.CHILD)  # 자녀가 먼저 코드를 만들어도 된다
    assert _err(resolve_pair(PairRequest("X", Role.CHILD, invite_code=code), reg, **T)) == "UNAUTHORIZED"  # 같은 역할
    assert resolve_pair(PairRequest("P", Role.PARENT, invite_code=code), reg, **T).outcome == "paired"
    assert reg.role_of(pid, "P") is Role.PARENT and reg.role_of(pid, "C") is Role.CHILD
    assert reg.issue_invite("C", Role.PARENT) is None  # 역할 바꿔 새 페어 불가


# ---- N-02 ----


def _consent(state, role, enabled, rev, caps=()):
    upd = ConsentUpdate("p1", None, role, Purpose.ANALYSIS, enabled, rev, frozenset(caps))
    return resolve_consent(upd, ActorContext(role.value, "p1", role), state, **T)


ALL = [Capability.MIC, Capability.CAPTURE, Capability.OVERLAY]


def test_02_1_allowed_02_2_blocked():
    s = ConsentState()
    assert _consent(s, Role.CHILD, True, 0).outcome == "blocked"  # 한쪽만 동의
    r = _consent(s, Role.PARENT, True, 1, ALL)
    assert r.outcome == "allowed" and r.data.stt_allowed and r.data.consent_revision == 2
    assert _consent(s, Role.PARENT, True, 2, ALL).data.consent_revision == 2  # 같은 값 재전송: 버전 유지
    r = _consent(s, Role.PARENT, True, 2, [Capability.MIC, Capability.CAPTURE])  # 표시만 거부
    assert r.outcome == "blocked" and r.data.stt_allowed and not r.data.overlay_allowed
    r = _consent(s, Role.CHILD, False, 3)  # 자녀 철회
    assert not r.data.stt_allowed and not r.data.capture_allowed


def test_02_3_other_pair_or_stale_revision():
    s = ConsentState()
    _consent(s, Role.CHILD, True, 0)
    assert _err(_consent(s, Role.PARENT, True, 0, ALL)) == "STALE_BASIS"  # 자녀가 먼저 바꿈 → 재조회
    assert s.revision == 1 and Role.PARENT not in s.enabled
    upd = ConsentUpdate("p2", None, Role.CHILD, Purpose.ANALYSIS, True, 1, frozenset())
    assert _err(resolve_consent(upd, ActorContext("C", "p1", Role.CHILD), s, **T)) == "UNAUTHORIZED"


# ---- N-03 ----

PARENT, CHILD = ActorContext("P", "p1", Role.PARENT), ActorContext("C", "p1", Role.CHILD)


def _call(reg, op, actor, cid=None):
    return start_or_close_call(op, actor, reg, call_id=cid, source=Source.FIXTURE)


def test_03_1_active_and_b01_3_duplicate_accept():
    reg = CallRegistry()
    cid = _call(reg, Operation.START, PARENT).data.call_id
    assert _err(_call(reg, Operation.START, PARENT)) == "DUPLICATE"  # 새 방 없음
    _call(reg, Operation.ACCEPT, CHILD, cid)
    _call(reg, Operation.ACCEPT, CHILD, cid)
    assert _call(reg, Operation.JOINED, PARENT, cid).outcome == "waiting"
    r = _call(reg, Operation.JOINED, CHILD, cid)
    assert r.outcome == "active" and r.data.connected_roles == (Role.PARENT, Role.CHILD)
    assert len(reg.calls) == 1


def test_03_child_can_call_and_caller_cannot_self_accept():
    reg = CallRegistry()
    cid = _call(reg, Operation.START, CHILD).data.call_id
    assert _err(_call(reg, Operation.ACCEPT, CHILD, cid)) == "UNAUTHORIZED"  # 자기 발신 수락 불가
    _call(reg, Operation.ACCEPT, PARENT, cid)
    _call(reg, Operation.JOINED, CHILD, cid)
    assert _call(reg, Operation.JOINED, PARENT, cid).outcome == "active"


def test_03_2_ended_is_idempotent():
    reg = CallRegistry()
    cid = _call(reg, Operation.START, PARENT).data.call_id
    assert _call(reg, Operation.END, CHILD, cid).data.call_state is CallState.ENDED
    assert _call(reg, Operation.END, CHILD, cid).outcome == "ended"
    assert _err(_call(reg, Operation.ACCEPT, CHILD, cid)) == "ALREADY_ENDED"


def test_03_4_accept_timeout_and_other_pair():
    t = [0.0]
    reg = CallRegistry(clock=lambda: t[0])
    cid = _call(reg, Operation.START, PARENT).data.call_id
    assert _err(_call(reg, Operation.ACCEPT, ActorContext("X", "p9", Role.CHILD), cid)) == "UNAUTHORIZED"
    t[0] = 31
    assert _err(_call(reg, Operation.ACCEPT, CHILD, cid)) == "TIMEOUT"
    assert reg.calls[cid].state is CallState.ENDED and "p1" not in reg.active_by_pair


def test_03_unanswered_call_does_not_block_redial():
    t = [0.0]
    reg = CallRegistry(clock=lambda: t[0])
    first = _call(reg, Operation.START, PARENT).data.call_id
    _call(reg, Operation.ACCEPT, CHILD, first)  # 수락했지만 RTC 참가 실패
    t[0] = 31
    second = _call(reg, Operation.START, PARENT)
    assert second.outcome == "waiting" and second.data.call_id != first
    assert reg.calls[first].end_reason.value == "RTC_FAILED"


def test_03_same_trace_id_for_whole_call():
    reg = CallRegistry()
    start = _call(reg, Operation.START, PARENT)
    cid, tid = start.data.call_id, start.diagnostics.trace_id
    later = [_call(reg, op, who, cid) for op, who in
             ((Operation.ACCEPT, CHILD), (Operation.JOINED, PARENT), (Operation.JOINED, CHILD), (Operation.END, CHILD))]
    assert {r.diagnostics.trace_id for r in later} == {tid} and reg.calls[cid].trace_id == tid
    stranger = _call(reg, Operation.END, ActorContext("X", "p9", Role.PARENT), cid)
    assert stranger.diagnostics.trace_id != tid  # 다른 페어에는 노출하지 않음
    assert _call(reg, Operation.START, PARENT).diagnostics.trace_id != tid  # 새 통화는 새 ID


# ---- N-04 ----


def _chunk(seq, who="p1:CHILD", kind=SourceKind.MICROPHONE, track="tc", call="c1"):
    return AudioChunk(call, track, who, kind, b"\x00" * 4, seq)


def test_04_1_ready_04_2_ignored():
    a = AudioIngest("p1", "c1")
    assert a.ingest(_chunk(0), stt_allowed=True, **T).outcome == "audio_ready"
    for c in (_chunk(0), _chunk(1, kind=SourceKind.CAMERA), _chunk(2, track="other"), _chunk(3, who="agent")):
        assert a.ingest(c, stt_allowed=True, **T).outcome == "ignored_input"
    assert a.ingest(_chunk(4), stt_allowed=False, **T).outcome == "ignored_input"
    a.close()
    assert a.ingest(_chunk(5), stt_allowed=True, **T).outcome == "ignored_input"
    assert a.drain(Role.CHILD) == []


def test_04_3_overflow_04_4_other_pair():
    a = AudioIngest("p1", "c1")
    results = [a.ingest(_chunk(i), stt_allowed=True, **T) for i in range(51)]
    assert results[-1].status is Status.PARTIAL and results[-1].outcome == "audio_gap"
    assert _err(a.ingest(_chunk(99, who="p2:CHILD"), stt_allowed=True, **T)) == "UNAUTHORIZED"
    assert _err(a.ingest(_chunk(99, call="c2"), stt_allowed=True, **T)) == "UNAUTHORIZED"


# ---- N-05 ----

REQ = TurnRequest("w", Role.CHILD, "c1", "turn1", 3000)


def test_05_stt_outcomes():
    ok = transcribe_korean_turn(REQ, SttEvent(SttKind.FINAL, "내일 아침 6시", 0.9), **T)
    assert ok.outcome == "transcribed" and ok.data.finality is Finality.FINAL
    assert "6시" not in repr(ok.data)  # 원문이 repr·로그에 나오지 않음
    assert transcribe_korean_turn(REQ, SttEvent(SttKind.NO_SPEECH), **T).outcome == "no_speech"
    low = transcribe_korean_turn(REQ, SttEvent(SttKind.FINAL, "아니 화면", 0.3), **T)
    assert low.status is Status.PARTIAL and low.outcome == "transcript_uncertain"
    assert _err(transcribe_korean_turn(REQ, None, **T)) == "TIMEOUT"  # no_speech로 바꾸지 않음
    assert _err(transcribe_korean_turn(REQ, SttEvent(SttKind.ERROR), **T)) == "DEPENDENCY_UNAVAILABLE"


# ---- N-06 ----


OK = ConsentView(True, True, True, True, 2)


def _fin(seq, role=Role.CHILD, fin=Finality.FINAL, turn=None, call="c1", rev=2):
    return FinalizeRequest(call, turn or f"t{seq}", seq, role, fin, "디스플레이 눌러", None, rev)


def test_06_completion_criteria_only_one_downstream():
    f = TurnFinalizer("p1", "c1")
    outs = [
        f.finalize(_fin(1, fin=Finality.PARTIAL), OK, **T).outcome,
        f.finalize(_fin(1), OK, **T).outcome,
        f.finalize(_fin(1), OK, **T).outcome,
    ]
    assert outs == ["partial_ignored", "final_committed", "duplicate_ignored"]
    assert _err(f.finalize(_fin(0, turn="old"), OK, **T)) == "STALE_BASIS"  # 역순
    f.close()
    assert _err(f.finalize(_fin(2), OK, **T)) == "STALE_BASIS"  # 종료 후 도착


def test_e07_parent_does_not_trigger():
    f = TurnFinalizer("p1", "c1")
    r = f.finalize(_fin(1, role=Role.PARENT), OK, **T)
    assert r.outcome == "final_committed" and r.data.triggered is False
    assert f.finalize(_fin(1, role=Role.CHILD, turn="c"), OK, **T).data.triggered is True


def test_06_4_consent_revoked_or_changed_mid_turn():
    f = TurnFinalizer("p1", "c1")
    revoked = ConsentView(False, False, False, False, 3)
    assert _err(f.finalize(_fin(1), revoked, **T)) == "STALE_BASIS"
    assert _err(f.finalize(_fin(1, rev=1), OK, **T)) == "STALE_BASIS"  # STT 시작 뒤 동의 버전이 바뀜
    assert f.finalize(_fin(1), OK, **T).outcome == "final_committed"


# ---- worker: RTZR 이벤트 → N-05 ----


def test_worker_maps_rtzr_events_and_hides_text(capsys):
    import asyncio

    from livekit.agents import stt as lkstt

    from hyojason_voice.worker import _recognize

    def ev(t, text):
        return lkstt.SpeechEvent(type=t, alternatives=[lkstt.SpeechData(language="ko", text=text)])

    async def fake():
        yield lkstt.SpeechEvent(type=lkstt.SpeechEventType.START_OF_SPEECH)
        yield ev(lkstt.SpeechEventType.INTERIM_TRANSCRIPT, "디스플")
        yield ev(lkstt.SpeechEventType.FINAL_TRANSCRIPT, "디스플레이 눌러 줘")
        raise ConnectionError("rtzr down")

    asyncio.run(_recognize(Role.CHILD, "c1", fake(), show_text=False))
    out = capsys.readouterr().out.splitlines()
    assert out == ["[CHILD] transcribed FINAL HIGH (10자)", "[CHILD] error DEPENDENCY_UNAVAILABLE: ConnectionError"]
    assert "디스플" not in "\n".join(out)  # --print-text 없으면 원문 없음
