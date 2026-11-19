"""N-23 · purge_help_and_restart — docs/small-nodes/N-23.md 최소 검증 23.1~23.5와 구현 완료 기준.

MOCK: N-11 상태 저장(InMemoryHelpStore), N-12·B-05·B-02 저장소(ScopedBuffer),
WS 제어 채널(RecordingEventSink). N-22 장부와 N-23 종료 기록은 실제 구현.
"""

from __future__ import annotations

import itertools
import threading

import pytest

from fixtures import PAIR, OTHER_PAIR, TRACE, post_call, snapshot
from hyojason_session.client_cleanup import ClientHelpCleanup, LocalAction
from hyojason_session.common.enums import ActorRole, CleanupScope, EndReason, Role, Source
from hyojason_session.common.result import Basis, Status
from hyojason_session.completion import CompletionLedger
from hyojason_session.mocks import InMemoryHelpStore, RecordingEventSink, ScopedBuffer
from hyojason_session.purge import (
    SERVER_SCOPES,
    PurgeInput,
    TombstoneRegistry,
    purge_help_and_restart,
)


class Env:
    def __init__(self, *, online=None, fail_trail=False):
        self.help_store = InMemoryHelpStore()
        self.trail = ScopedBuffer(CleanupScope.NAVIGATION_TRAIL, fail_on_purge=fail_trail)
        self.inference = ScopedBuffer(CleanupScope.INFERENCE_RESULTS)
        self.utterances = ScopedBuffer(CleanupScope.PENDING_UTTERANCES)
        self.ledger = CompletionLedger()
        self.tombstones = TombstoneRegistry()
        self.events = RecordingEventSink(online=online)
        self._ids = (f"end-{i}" for i in itertools.count(1))

    @property
    def stores(self):
        return [self.help_store, self.trail, self.inference, self.utterances, self.ledger]

    def seed(self, snap):
        self.help_store.put_snapshot(snap)
        for buf, ref in ((self.trail, "edge-1"), (self.inference, "run-17-a"), (self.utterances, "utt-9")):
            buf.put(snap.help_id, ref)
        return snap

    def purge(self, help_id="help-1", reason=EndReason.CHILD_COMPLETED, role=ActorRole.CHILD, *,
              rtc_active=True, consent_active=True, pair_id=PAIR, current="auto", stores=None):
        if current == "auto":
            latest = self.help_store.latest_help_id(pair_id)
            current = self.help_store.get_snapshot(latest) if latest else None
        return purge_help_and_restart(
            PurgeInput(help_id, reason, rtc_active, role, consent_active),
            pair_id=pair_id, current=current, stores=self.stores if stores is None else stores,
            tombstones=self.tombstones, events=self.events, trace_id=TRACE, source=Source.FIXTURE,
            new_event_id=lambda: next(self._ids),
        )

    def remaining(self, help_id="help-1"):
        return {
            "snapshot": self.help_store.get_snapshot(help_id) is not None,
            "trail": self.trail.count(help_id),
            "inference": self.inference.count(help_id),
            "utterances": self.utterances.count(help_id),
        }


# --- 23.1 현재 도움의 승인된 종료와 정리 -------------------------------------------


def test_23_1_help_ended_clears_server_and_notifies_both():
    env = Env()
    env.seed(post_call())
    r = env.purge()
    assert (r.status, r.outcome) == (Status.SUCCESS, "help_ended")
    assert env.remaining() == {"snapshot": False, "trail": 0, "inference": 0, "utterances": 0}
    assert set(r.data.cleared_scopes) == set(SERVER_SCOPES) | {
        CleanupScope.PARENT_DEVICE, CleanupScope.CHILD_DEVICE}
    assert (r.data.end_event_id, r.data.end_reason) == ("end-1", EndReason.CHILD_COMPLETED)
    assert r.data.resume_requires_consent is False  # 자녀 완료 + 동의 유효 → 새 발화 대기
    assert r.diagnostics.steps["next_help"].code == "WAIT_NEW_CHILD_TURN"

    for role in (Role.PARENT, Role.CHILD):
        [ev] = env.events.events_of(role, "help.ended")
        assert (ev.v, ev.pair_id, ev.call_id, ev.help_id) == (1, PAIR, "call-1", "help-1")
        assert dict(ev.payload) == {"end_event_id": "end-1", "reason": "CHILD_COMPLETED"}

    t = env.tombstones.get("help-1")
    assert (t.end_event_id, t.end_reason, t.final_event_seq) == ("end-1", EndReason.CHILD_COMPLETED, 13)


@pytest.mark.parametrize(("reason", "role", "consent", "requires"), [
    (EndReason.PARENT_STOPPED, ActorRole.PARENT, True, True),  # 부모 중단 → 재동의 전 새 도움 없음
    (EndReason.CHILD_COMPLETED, ActorRole.CHILD, False, True),  # 동의 철회됨
    (EndReason.NO_GOAL_AT_HANGUP, ActorRole.AGENT, True, False),
    (EndReason.CONSENT_REVOKED, ActorRole.CHILD, True, True),  # N-11 동의 철회 종료
])
def test_23_1_resume_consent_rule(reason, role, consent, requires):
    env = Env()
    env.seed(snapshot())
    r = env.purge(reason=reason, role=role, consent_active=consent)
    assert r.outcome == "help_ended" and r.data.resume_requires_consent is requires
    assert env.tombstones.latest_for_pair(PAIR).resume_requires_consent is requires
    if requires:
        assert r.diagnostics.steps["next_help"].code == "WAIT_PARENT_CONSENT"


def test_23_1_call_already_ended_reports_no_next_help():
    env = Env()
    env.seed(post_call())
    r = env.purge(rtc_active=False)
    assert r.diagnostics.steps["next_help"].code == "CALL_ENDED"


# --- 23.2 같은 도움을 두 번 종료 ---------------------------------------------------


def test_23_2_second_end_has_no_side_effects():
    env = Env()
    env.seed(post_call())
    first = env.purge()
    sent_before = list(env.events.sent)
    second = env.purge(current=None)
    assert (second.status, second.outcome) == (Status.SUCCESS, "already_ended")
    assert second.data.end_event_id == first.data.end_event_id
    assert second.data.cleared_scopes == ()
    assert env.events.sent == sent_before  # help.ended 재발행 없음


def test_23_2_concurrent_purges_end_once():
    env = Env()
    env.seed(post_call())
    current = env.help_store.get_snapshot("help-1")
    results, barrier = [], threading.Barrier(6)

    def go():
        barrier.wait()
        results.append(env.purge(current=current))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.outcome for r in results) == ["already_ended"] * 5 + ["help_ended"]
    assert len(env.events.events_of(Role.PARENT, "help.ended")) == 1
    assert len(env.events.events_of(Role.CHILD, "help.ended")) == 1


# --- 23.3 서버는 종료했으나 단말 연결이 끊김 ----------------------------------------


def test_23_3_offline_child_leaves_local_cleanup_pending():
    env = Env(online={Role.CHILD: False})
    env.seed(post_call())
    r = env.purge()
    assert (r.status, r.outcome) == (Status.PARTIAL, "local_cleanup_pending")
    assert r.diagnostics.terminal_error is None  # partial의 단계 실패는 steps에만
    assert r.diagnostics.steps["notify_child"].code == "DEPENDENCY_UNAVAILABLE"
    assert CleanupScope.CHILD_DEVICE not in r.data.cleared_scopes
    assert CleanupScope.PARENT_DEVICE in r.data.cleared_scopes
    # 서버 쪽은 이미 정리 완료, 종료 기록도 있음 → 재접속(N-13)에서 ENDED로 정리된다
    assert env.remaining()["snapshot"] is False and env.tombstones.get("help-1") is not None


# --- 23.4 이전/다른 도움을 대상으로 요청 -------------------------------------------


def test_23_4_other_help_request_keeps_current_data():
    env = Env()
    env.seed(snapshot(help_id="help-2"))
    r = env.purge(help_id="help-unknown")
    assert (r.status, r.outcome, r.data) == (Status.ERROR, None, None)
    assert r.diagnostics.terminal_error.code == "STALE_BASIS"
    assert env.remaining("help-2") == {"snapshot": True, "trail": 1, "inference": 1, "utterances": 1}
    assert env.events.sent == []


def test_23_4_other_pair_is_rejected():
    env = Env()
    env.seed(snapshot())
    env.purge()
    r = env.purge(pair_id=OTHER_PAIR, current=None)  # 다른 페어가 끝난 도움을 다시 요청
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "UNAUTHORIZED"


@pytest.mark.parametrize(("reason", "role"), [
    (EndReason.CHILD_COMPLETED, ActorRole.PARENT),
    (EndReason.PARENT_STOPPED, ActorRole.CHILD),
    (EndReason.NO_GOAL_AT_HANGUP, ActorRole.CHILD),
    (EndReason.CONSENT_REVOKED, ActorRole.AGENT),
])
def test_end_reason_and_role_cannot_be_swapped(reason, role):
    env = Env()
    env.seed(snapshot())
    r = env.purge(reason=reason, role=role)
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "UNAUTHORIZED"
    assert env.remaining()["snapshot"] is True


def test_missing_server_scope_is_rejected_before_any_side_effect():
    env = Env()
    env.seed(snapshot())
    r = env.purge(stores=[env.help_store, env.ledger])  # 이력·추론·발화 저장소 미연결
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "INVALID_INPUT"
    assert env.remaining() == {"snapshot": True, "trail": 1, "inference": 1, "utterances": 1}


def test_purge_failure_is_retryable_and_retry_completes():
    env = Env(fail_trail=True)
    env.seed(snapshot())
    r = env.purge()
    assert r.status is Status.ERROR
    assert (r.diagnostics.terminal_error.code, r.diagnostics.terminal_error.retryable) == (
        "DEPENDENCY_UNAVAILABLE", True)
    assert env.tombstones.get("help-1") is None and env.events.sent == []
    assert env.remaining()["snapshot"] is True  # HELP_STATE는 마지막에 지워 재시도 가능
    env.trail.fail_on_purge = False
    assert env.purge().outcome == "help_ended"


# --- 23.5 경계: 이전 종료 이후 새로운 목표 (구현 완료 기준) ---------------------------


def test_23_5_new_goal_after_end_opens_a_clean_help():
    env = Env()
    old = env.seed(post_call())
    env.ledger.claim_end("help-1", EndReason.CHILD_COMPLETED)
    r = env.purge()
    assert r.outcome == "help_ended"
    b = r.diagnostics.basis
    assert b == Basis.from_snapshot(old)
    assert (b.pair_id, b.help_id, b.goal_revision, b.mode_revision) == (PAIR, "help-1", 1, 2)

    # 같은 통화에서 새 자녀 목표 → N-11(MOCK)이 새 help_id로 연다
    new = snapshot(help_id="help-2", event_seq=1)
    env.help_store.put_snapshot(new)
    assert env.remaining("help-1") == {"snapshot": False, "trail": 0, "inference": 0, "utterances": 0}
    assert env.remaining("help-2") == {"snapshot": True, "trail": 0, "inference": 0, "utterances": 0}
    assert env.ledger.end_claim("help-2") is None  # 이전 종료 승인을 물려받지 않음

    # 늦게 온 이전 도움 종료 요청은 새 도움을 건드리지 않는다
    late = env.purge(help_id="help-1", current=new)
    assert late.outcome == "already_ended"
    assert env.help_store.get_snapshot("help-2") is not None


# --- 클라이언트 참조 구현 (A·B Kotlin 이식용) ----------------------------------------


def test_client_parent_stop_clears_locally_before_network():
    parent = ClientHelpCleanup(Role.PARENT)
    parent.start_help("help-1")
    actions = parent.parent_stop_pressed()
    assert actions[-1] is LocalAction.SEND_STOP
    assert actions[:-1] == [LocalAction.STOP_CAPTURE, LocalAction.CLEAR_COORDINATES,
                            LocalAction.CANCEL_TIMERS, LocalAction.CLEAR_OVERLAY, LocalAction.CLEAR_BANNER]
    assert parent.parent_stop_pressed() == []  # 이미 정리됨


def test_client_completion_message_once_and_only_for_child_complete():
    env = Env()
    env.seed(post_call())
    env.purge()
    [ended] = env.events.events_of(Role.PARENT, "help.ended")
    parent, child = ClientHelpCleanup(Role.PARENT), ClientHelpCleanup(Role.CHILD)
    parent.start_help("help-1")
    child.start_help("help-1")
    assert LocalAction.SHOW_COMPLETION in parent.on_help_ended(ended)
    assert LocalAction.SHOW_COMPLETION not in parent.on_help_ended(ended)  # 한 번만
    assert child.on_help_ended(ended) == [LocalAction.CLEAR_COMPLETE_BUTTON,
                                          LocalAction.CLEAR_ARRIVAL_NOTICE]

    stop_env = Env()
    stop_env.seed(snapshot())
    stop_env.purge(reason=EndReason.PARENT_STOPPED, role=ActorRole.PARENT)
    [stopped] = stop_env.events.events_of(Role.PARENT, "help.ended")
    p2 = ClientHelpCleanup(Role.PARENT)
    p2.start_help("help-1")
    assert LocalAction.SHOW_COMPLETION not in p2.on_help_ended(stopped)


def test_client_ignores_late_help_ended_for_other_help():
    env = Env()
    env.seed(post_call())
    env.purge()
    [ended] = env.events.events_of(Role.CHILD, "help.ended")
    child = ClientHelpCleanup(Role.CHILD)
    child.start_help("help-2")  # 이미 새 도움
    assert child.on_help_ended(ended) == [] and child.active is True
