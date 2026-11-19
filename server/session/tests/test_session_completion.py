"""실제 N-11(SessionControl) 위의 종료·도착 흐름 — 결정 메모 5 (가) 계약 흐름.

완료·중단: N-22 승인 → N-11 help.end → N-23 정리·help.ended
도착: N-22 검증·중복 제거 → N-11 arrival.suspected/revoked
N-11 내부 종료(동의 철회, 목표 없는 통화 종료)도 help.purge → N-23

MOCK: WS 제어 채널(RecordingEventSink), N-01 페어 확인(StaticPairDirectory). RTC·AVD·모델 없음.
"""

from __future__ import annotations

from itertools import count

import pytest

from hyojason_session.client_cleanup import ClientHelpCleanup, LocalAction
from hyojason_session.common.decisions import ArrivalDecision
from hyojason_session.common.enums import CleanupScope, EndReason, HelpCommand, Role, Source
from hyojason_session.common.types import ActorContext
from hyojason_session.control import SessionControl
from hyojason_session.help_state import Actor, Event
from hyojason_session.mocks import RecordingEventSink, ScopedBuffer, StaticPairDirectory
from hyojason_session.navigation import NavigationCatalog
from hyojason_session.reconnect import ReconnectRequest, ResumeState, reconcile_reconnect

PARENT = ActorContext("parent", "pair", Role.PARENT)
CHILD = ActorContext("child", "pair", Role.CHILD)
PAIRS = StaticPairDirectory({"pair": {"parent": Role.PARENT, "child": Role.CHILD}})


class Flow:
    def __init__(self, *, online=None):
        self.ids = count(1)
        self.events = RecordingEventSink(online=online)
        self.inference = ScopedBuffer(CleanupScope.INFERENCE_RESULTS)
        catalog = NavigationCatalog(
            screen_apps={"settings_home": "settings", "settings_display": "settings"},
            target_ids={"settings.display"},
            evidence_codes={"STABLE_CONTROLS", "timeout_10m_selected"},
            reversible_pairs={("settings_home", "settings_display")},
        )
        self.control = SessionControl(
            "pair", "call", "help", catalog=catalog,
            approved_slots={"SET_TIMEOUT": lambda slots: slots == {}}, source="FIXTURE",
            events=self.events, result_stores=[self.inference],
        )
        self.emit("call.connected")
        for role in ("PARENT", "CHILD"):
            self.emit("consent.updated", role, purpose="analysis", enabled=True)
        self.emit("projection.started", "PARENT", projection_epoch="capture")
        self.screen()

    def apply(self, kind, role="SERVER", **payload):
        b = self.control.snapshot.basis
        event = Event(kind, f"e{next(self.ids)}", b.pair_id, b.call_id, b.help_id, payload)
        return self.control.apply(event, Actor(role, b.pair_id, b.call_id), b)

    def emit(self, kind, role="SERVER", **payload):
        update = self.apply(kind, role, **payload)
        assert update.state.status == "success", update.state.diagnostics
        return update

    def screen(self, revision=1):
        return self.emit("screen.observed", "PARENT", projection_epoch="capture", screen_revision=revision,
                         screen_kind="settings_home", target_ids=["settings.display"],
                         evidence_codes=["STABLE_CONTROLS"], observation_health="ACTIVE")

    def turn(self, seq=1, *, goal=True):
        self.emit("speech.final", speaker_role="CHILD", utterance_id=f"t{seq}", utterance_seq=seq,
                  text="fixture-only utterance")
        self.emit("instruction.drafted", source_utterance_id=f"t{seq}", action="TAP",
                  target_key="settings.display", supersedes_previous=True)
        if goal:
            self.emit("goal.drafted", source_utterance_id=f"t{seq}", goal_id="SET_TIMEOUT", approved_slots={})
        self.inference.put(self.control.snapshot.help_id, "run-27-a")

    def post_call(self):
        self.turn()
        self.emit("call.ended")
        self.screen()  # 통화 후 새 관찰

    def arrival(self, arrival_id="arr-1", arrived=True):
        b = self.control.snapshot.basis
        return self.control.submit_arrival(ArrivalDecision(
            arrival_id, arrived, b.goal_revision, ("timeout_10m_selected",) if arrived else (), b))

    def ended_events(self, role):
        return self.events.events_of(role, "help.ended")


# --- 완료: N-22 → N-11 → N-23 --------------------------------------------------


def test_child_complete_flows_n22_n11_n23_without_goal_revision():
    f = Flow()
    f.turn()
    r = f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help")  # goal_revision 모름 → 기본 0
    assert r.completion.outcome == "end_requested"
    assert r.update.state.outcome == "state_applied"
    assert r.update.purge.outcome == "help_ended"
    snap = f.control.snapshot
    assert (snap.help_state, snap.end_reason) == ("ENDED", EndReason.CHILD_COMPLETED)
    assert (snap.goal, snap.instruction, snap.screen) == (None, None, None)
    assert f.control.trail.records == () and f.inference.count("help") == 0
    # 같은 trace로 N-22 → N-11 → N-23 추적
    assert r.update.state.diagnostics.input_refs == (r.completion.ref,)
    assert r.update.purge.diagnostics.input_refs == (r.update.state.ref,)
    assert len({r.completion.diagnostics.trace_id, r.update.state.diagnostics.trace_id,
                r.update.purge.diagnostics.trace_id}) == 1
    for role in (Role.PARENT, Role.CHILD):
        [ev] = f.ended_events(role)
        assert dict(ev.payload) == {"end_event_id": r.update.purge.data.end_event_id,
                                    "reason": "CHILD_COMPLETED"}
    assert r.update.purge.diagnostics.source is Source.FIXTURE


def test_second_complete_and_late_stop_have_no_side_effects():
    f = Flow()
    f.turn()
    f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help")
    again = f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help")
    stop = f.control.submit_command(HelpCommand.STOP, PARENT, "help")
    for r in (again, stop):
        assert (r.completion.outcome, r.update) == ("duplicate_ignored", None)
    assert len(f.ended_events(Role.PARENT)) == 1 and len(f.ended_events(Role.CHILD)) == 1


def test_parent_stop_removes_parent_consent_and_gates_next_help():
    f = Flow()
    f.turn()
    r = f.control.submit_command(HelpCommand.STOP, PARENT, "help")
    assert r.update.purge.data.resume_requires_consent is True
    assert f.control.tombstones.latest_for_pair("pair").resume_requires_consent is True
    # 부모가 다시 안내를 켜기 전에는 새 자녀 발화로 도움이 열리지 않는다 (B07.3)
    blocked = f.apply("speech.final", speaker_role="CHILD", utterance_id="t2", utterance_seq=2, text="x")
    assert blocked.state.diagnostics.terminal_error.code == "NO_CONSENT"


@pytest.mark.parametrize(("command", "actor"), [
    (HelpCommand.COMPLETE, PARENT), (HelpCommand.STOP, CHILD),
    (HelpCommand.COMPLETE, ActorContext("stranger", "pair-x", Role.CHILD)),
])
def test_wrong_role_or_pair_changes_nothing(command, actor):
    f = Flow()
    f.turn()
    before = f.control.snapshot
    r = f.control.submit_command(command, actor, "help")
    assert r.completion.diagnostics.terminal_error.code == "UNAUTHORIZED" and r.update is None
    assert f.control.snapshot == before and f.events.sent == []


def test_raw_client_complete_event_is_not_an_n11_input():
    f = Flow()
    f.turn()
    assert f.apply("help.complete", "CHILD").state.status == "error"
    assert f.apply("help.end", "CHILD", reason="CHILD_COMPLETED").state.diagnostics.terminal_error.code == (
        "UNAUTHORIZED")


def test_old_help_complete_after_new_help_is_stale():
    f = Flow()
    f.turn()
    f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help")
    f.emit("help.link_approved", assisted_role="PARENT")  # 새 코드 승인 → N-11이 새 help_id로 연다 (#6)
    f.turn(2, goal=False)
    new_help = f.control.snapshot.help_id
    assert new_help != "help"
    r = f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help")
    assert r.completion.diagnostics.terminal_error.code == "STALE_BASIS"
    assert f.control.snapshot.help_id == new_help and f.control.snapshot.help_state == "ACTIVE"


# --- N-11 내부 종료도 N-23을 거친다 -----------------------------------------------


def test_consent_revoked_end_is_purged_and_announced():
    f = Flow()
    f.turn()
    update = f.emit("consent.updated", "CHILD", purpose="analysis", enabled=False)
    assert update.purge.outcome == "help_ended"
    assert update.purge.data.end_reason is EndReason.CONSENT_REVOKED
    assert update.purge.data.resume_requires_consent is True
    assert f.inference.count("help") == 0
    assert [e.payload["reason"] for e in f.ended_events(Role.PARENT)] == ["CONSENT_REVOKED"]


def test_hangup_without_goal_is_purged_and_announced():
    f = Flow()
    f.turn(goal=False)
    update = f.emit("call.ended")
    assert update.purge.data.end_reason is EndReason.NO_GOAL_AT_HANGUP
    assert update.purge.diagnostics.steps["next_help"].code == "CALL_ENDED"
    assert len(f.ended_events(Role.CHILD)) == 1


def test_offline_child_leaves_cleanup_pending_and_reconnect_clears():
    f = Flow(online={Role.CHILD: False})
    f.turn()
    r = f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help")
    assert (r.update.purge.status, r.update.purge.outcome) == ("partial", "local_cleanup_pending")
    back = reconcile_reconnect(ReconnectRequest("pair", "help", Role.CHILD, 3, "re"), actor=CHILD,
                               pairs=PAIRS, store=f.control, trace_id="t", source=Source.FIXTURE)
    assert (back.outcome, back.data.resume_state) == ("no_active_help", ResumeState.ENDED)


# --- 도착: N-22 → N-11 -----------------------------------------------------------


def test_post_call_arrival_is_announced_once_then_revoked():
    f = Flow()
    f.post_call()
    first = f.arrival("arr-1")
    assert first.completion.outcome == "arrival_announced"
    assert f.control.snapshot.help_state == "ARRIVAL_PENDING"
    assert "arrival.suspected" in [e.kind for e in first.update.state.data.side_effects]
    assert f.arrival("arr-1").update is None  # 같은 도착: N-11에 다시 넣지 않음
    assert f.arrival("arr-9").update is None  # 같은 목표에 이미 알림

    revoked = f.arrival("arr-2", arrived=False)  # 철회 판단은 새 id, 알림은 arr-1을 내린다
    assert revoked.completion.data.arrival_id == "arr-1"
    assert revoked.update.state.status == "success"
    assert f.control.snapshot.help_state == "ACTIVE"


def test_arrival_during_call_never_reaches_n11():
    f = Flow()
    f.turn()
    before = f.control.snapshot
    r = f.arrival()
    assert r.completion.diagnostics.terminal_error.code == "NOT_APPLICABLE" and r.update is None
    assert f.control.snapshot == before


def test_b07_2_arrival_twice_complete_twice_one_notice_one_end_one_message():
    f = Flow()
    f.post_call()
    notices = sum(f.arrival("arr-1").update is not None for _ in range(2))
    ends = sum(f.control.submit_command(HelpCommand.COMPLETE, CHILD, "help").update is not None
               for _ in range(2))
    assert (notices, ends) == (1, 1)
    parent_app = ClientHelpCleanup(Role.PARENT)
    parent_app.start_help("help")
    shown = sum(LocalAction.SHOW_COMPLETION in parent_app.on_help_ended(e)
                for e in f.ended_events(Role.PARENT) * 2)
    assert shown == 1
    tomb = f.control.get_tombstone("help")
    assert tomb.end_event_id == f.ended_events(Role.CHILD)[0].event_id
