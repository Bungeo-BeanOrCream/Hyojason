"""B-07 통합: N-21 → N-22 → N-23 → N-13을 한 흐름으로 묶어 B07.1~B07.4를 확인한다.

N-11은 아직 MOCK(InMemoryHelpStore)이라 상태 전이는 테스트가 직접 put_snapshot으로 만든다.
B-07 Big Node의 오케스트레이션(이벤트 라우팅)은 아직 없어 테스트가 노드를 순서대로 부른다.
"""

from __future__ import annotations

from fixtures import CHILD, CHILD_USER, PAIR, PARENT, PARENT_USER, TRACE, post_call, snapshot
from hyojason_session.client_cleanup import ClientHelpCleanup, LocalAction
from hyojason_session.common.decisions import ArrivalDecision
from hyojason_session.common.enums import (
    ActorRole,
    ArrivalState,
    CleanupScope,
    EndReason,
    HelpCommand,
    Role,
    Source,
)
from hyojason_session.common.result import Basis, Status
from hyojason_session.common.types import ActorContext
from hyojason_session.completion import CompletionInput, CompletionLedger, resolve_arrival_or_completion
from hyojason_session.mocks import InMemoryHelpStore, RecordingEventSink, ScopedBuffer, StaticPairDirectory
from hyojason_session.purge import PurgeInput, TombstoneRegistry, purge_help_and_restart
from hyojason_session.reconnect import ReconnectRequest, ResumeState, reconcile_reconnect
from hyojason_session.role_view import RoleViewInput, project_role_view
from hyojason_session.session_state import SessionStateReader
from hyojason_session.views import ArrivalIndicator

PAIRS = StaticPairDirectory({PAIR: {PARENT_USER: Role.PARENT, CHILD_USER: Role.CHILD}})


class Server:
    """테스트용 배선. 실제 서버에서는 B-04·B-07 Big Node가 이 순서를 맡는다."""

    def __init__(self, *, online=None):
        self.help_store = InMemoryHelpStore()
        self.ledger = CompletionLedger()
        self.tombstones = TombstoneRegistry()
        self.events = RecordingEventSink(online=online)
        self.reader = SessionStateReader(self.help_store, self.ledger, self.tombstones)
        self.stores = [self.help_store, self.ledger] + [
            ScopedBuffer(s) for s in (CleanupScope.NAVIGATION_TRAIL, CleanupScope.INFERENCE_RESULTS,
                                      CleanupScope.PENDING_UTTERANCES)]
        self.announcements = 0

    def current(self):
        latest = self.reader.latest_help_id(PAIR)
        return self.reader.get_snapshot(latest) if latest else None

    def arrival(self, arrival_id):
        snap = self.current()
        b = Basis.from_snapshot(snap)
        decision = ArrivalDecision(arrival_id, True, b.goal_revision, ("alarm_card_active",), b)
        r = resolve_arrival_or_completion(
            CompletionInput(decision, None, snap.help_id, b.goal_revision, ActorRole.AGENT, snap),
            actor=None, ledger=self.ledger, trace_id=TRACE, source=Source.FIXTURE)
        if r.outcome == "arrival_announced":
            self.announcements += 1  # 실제로는 arrival.suspected를 자녀에게 보낸다
        return r

    def command(self, cmd, actor, help_id=None):
        role = ActorRole(actor.role.value)
        latest = self.reader.latest_help_id(PAIR)
        target = help_id or latest
        if target == latest and self.tombstones.get(target) is not None:
            # B-07 정책 3: 방금 끝난(아직 최신인) 도움의 명령은 N-23으로 → already_ended, 화면 효과 0
            reason = {HelpCommand.COMPLETE: EndReason.CHILD_COMPLETED,
                      HelpCommand.STOP: EndReason.PARENT_STOPPED}[cmd]
            return purge_help_and_restart(
                PurgeInput(target, reason, False, role, True),
                pair_id=PAIR, current=None, stores=self.stores, tombstones=self.tombstones,
                events=self.events, trace_id=TRACE, source=Source.FIXTURE)
        snap = self.current()
        r = resolve_arrival_or_completion(
            CompletionInput(None, cmd, help_id or snap.help_id, 1, role, snap),
            actor=actor, ledger=self.ledger, trace_id=TRACE, source=Source.FIXTURE)
        if r.status is Status.SUCCESS and r.data.end_requested:  # N-22 → N-23 한 번
            purge_help_and_restart(
                PurgeInput(snap.help_id, r.data.end_reason, False, role, True),
                pair_id=PAIR, current=snap, stores=self.stores, tombstones=self.tombstones,
                events=self.events, trace_id=TRACE, source=Source.FIXTURE, input_refs=(r.ref,))
        return r

    def view(self, actor):
        snap = self.current()
        return project_role_view(
            RoleViewInput(snap, actor.role, self.reader.arrival_state(snap.help_id), True),
            actor=actor, latest_help_id=snap.help_id, trace_id=TRACE)

    def reconnect(self, actor, help_id="help-1"):
        return reconcile_reconnect(
            ReconnectRequest(PAIR, help_id, actor.role, 10, "snap-req"),
            actor=actor, pairs=PAIRS, store=self.reader, trace_id=TRACE)


def test_b07_1_same_session_reconnect_parent_goal_child_minimal():
    s = Server()
    s.help_store.put_snapshot(snapshot())
    p, c = s.reconnect(PARENT), s.reconnect(CHILD)
    assert p.data.snapshot.parent_view.goal.goal_id == "CLOCK_ALARM"
    assert set(vars(c.data.snapshot.child_view)) == {"call_state", "can_complete", "arrival_indicator"}


def test_b07_2_two_arrivals_two_completes_one_notice_one_end_one_message():
    s = Server()
    s.help_store.put_snapshot(post_call())
    s.arrival("arr-1")
    s.arrival("arr-1")
    assert s.announcements == 1
    assert s.view(CHILD).data.child_view.arrival_indicator is ArrivalIndicator.SUSPECTED

    first, second = s.command(HelpCommand.COMPLETE, CHILD), s.command(HelpCommand.COMPLETE, CHILD)
    # 두 번째 누름은 이미 지워진 도움 → B-07이 N-23으로 보내 already_ended (종료 이벤트 재발행 없음)
    assert (first.outcome, second.outcome) == ("end_requested", "already_ended")
    parent_events = s.events.events_of(Role.PARENT, "help.ended")
    assert len(parent_events) == 1 and len(s.events.events_of(Role.CHILD, "help.ended")) == 1

    parent_app = ClientHelpCleanup(Role.PARENT)
    parent_app.start_help("help-1")
    shown = sum(LocalAction.SHOW_COMPLETION in parent_app.on_help_ended(e) for e in parent_events * 2)
    assert shown == 1  # 부모 완료 문구 1회

    # 끝난 뒤 재접속: 양쪽 모두 활성 도움 없음 → 로컬 정리
    for actor in (PARENT, CHILD):
        r = s.reconnect(actor)
        assert (r.outcome, r.data.resume_state, r.data.snapshot) == ("no_active_help", ResumeState.ENDED, None)


def test_b07_3_parent_stop_requires_consent_before_new_help():
    s = Server()
    s.help_store.put_snapshot(snapshot())
    assert s.command(HelpCommand.STOP, PARENT).outcome == "end_requested"
    # N-11이 새 자녀 지시로 도움을 열기 전에 확인할 조건 (N-11 구현 시 강제)
    assert s.tombstones.latest_for_pair(PAIR).resume_requires_consent is True
    parent_app = ClientHelpCleanup(Role.PARENT)
    parent_app.start_help("help-1")
    [ended] = s.events.events_of(Role.PARENT, "help.ended")
    assert LocalAction.SHOW_COMPLETION not in parent_app.on_help_ended(ended)


def test_b07_4_non_registered_or_old_help_complete_is_rejected():
    s = Server()
    s.help_store.put_snapshot(snapshot())
    stranger = ActorContext("user-stranger", "pair-stranger", Role.CHILD)
    assert s.command(HelpCommand.COMPLETE, stranger).diagnostics.terminal_error.code == "UNAUTHORIZED"

    s.command(HelpCommand.COMPLETE, CHILD)  # help-1 종료
    s.help_store.put_snapshot(post_call(help_id="help-2", event_seq=1))  # 새 도움
    old = s.command(HelpCommand.COMPLETE, CHILD, help_id="help-1")
    assert old.status is Status.ERROR and old.diagnostics.terminal_error.code == "STALE_BASIS"
    assert s.current().help_id == "help-2"  # 현재 도움 유지
    assert s.reader.arrival_state(s.current().help_id) is ArrivalState.NONE
