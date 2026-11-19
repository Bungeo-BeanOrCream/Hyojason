"""#6 준비: 지원 대상(assisted_role)을 자녀로 바꾼 경우와 새 도움 = 새 코드 승인 (G-3).

#6 수용 기준 중 D 쪽: 같은 발신 방향(부모 발신)에서 지원 대상을 바꾸면 관찰·안내 기기와 지시 발화자만
바뀌고, 상대 기기에는 안내·지시·화면 정보가 가지 않는다. 완료는 지원 대상의 상대, 안내 끄기는 지원 대상
(#6 제안 — 팀 확정 전이라 기본값 부모에서는 기존 동작과 같다). 결과는 모두 FIXTURE.
"""

from __future__ import annotations

import pytest

from fixtures import TRACE, post_call, snapshot
from hyojason_session.client_cleanup import ClientHelpCleanup, LocalAction
from hyojason_session.client_resume_gate import ClientAction, ClientResumeGate
from hyojason_session.common.enums import (
    ActorRole, ArrivalState, CleanupScope, EndReason, HelpCommand, HelpState, Role,
)
from hyojason_session.common.events import ControlEvent
from hyojason_session.common.types import ActorContext
from hyojason_session.completion import CompletionInput, CompletionLedger, resolve_arrival_or_completion
from hyojason_session.help_state import Actor, Event, HelpSession
from hyojason_session.mocks import InMemoryHelpStore, RecordingEventSink, ScopedBuffer, StaticPairDirectory
from hyojason_session.processing import Run
from hyojason_session.purge import PurgeInput, TombstoneRegistry, purge_help_and_restart
from hyojason_session.reconnect import ReconnectRequest, reconcile_reconnect
from hyojason_session.role_view import RoleViewInput, project_role_view
from hyojason_session.views import ParentStatus

PARENT = ActorContext("user-parent", "pair-fixture", Role.PARENT)
CHILD = ActorContext("user-child", "pair-fixture", Role.CHILD)
CHILD_HELP = dict(assisted_role=Role.CHILD)


# --- N-11 -----------------------------------------------------------------------


class Session:
    def __init__(self, assisted="CHILD"):
        self.n = 0
        self.s = HelpSession("pair", "call", "help", screen_apps={"settings_home": "settings"},
                             target_ids={"settings.display"}, evidence_codes={"VISIBLE"},
                             approved_slots={"SET_FONT": lambda slots: True}, assisted_role=assisted)
        self.send("call.connected")
        for role in ("PARENT", "CHILD"):
            self.send("consent.updated", role, purpose="analysis", enabled=True)

    def apply(self, kind, role="SERVER", **payload):
        self.n += 1
        b = self.s.snapshot.basis
        return self.s.reduce_help_state(Event(kind, f"e{self.n}", "pair", "call", b.help_id, payload),
                                        Actor(role, "pair", "call"), Run(b, source="FIXTURE", trace_id=TRACE))

    def send(self, *args, **kwargs):
        result = self.apply(*args, **kwargs)
        assert result.status == "success", result.diagnostics
        return result

    def code(self, *args, **kwargs):
        result = self.apply(*args, **kwargs)
        return result.diagnostics.terminal_error.code if result.status == "error" else result.outcome

    def speech(self, seq, speaker):
        return self.send("speech.final", speaker_role=speaker, utterance_id=f"t{seq}", utterance_seq=seq, text="x")

    @staticmethod
    def effects(result):
        return [(e.kind, e.recipient) for e in result.data.side_effects]


def test_n11_child_assisted_parent_speaks_instructions_child_device_observes():
    f = Session("CHILD")
    assert f.s.snapshot.assisted_role == "CHILD"
    assert ("turn.interpret", "SERVER") not in f.effects(f.speech(1, "CHILD"))  # 지원 대상 말은 지시가 아님
    assert ("turn.interpret", "SERVER") in f.effects(f.speech(2, "PARENT"))
    drafted = f.send("instruction.drafted", source_utterance_id="t2", action="TAP",
                     target_key="settings.display", supersedes_previous=True)
    assert ("instruction.accepted", "CHILD") in f.effects(drafted)
    assert f.code("projection.started", "PARENT", projection_epoch="e1") == "UNAUTHORIZED"
    assert f.code("projection.started", "CHILD", projection_epoch="e1") == "state_applied"
    assert f.code("screen.observed", "PARENT", projection_epoch="e1", screen_revision=1,
                  observation_health="ACTIVE", screen_kind="settings_home", target_ids=[],
                  evidence_codes=[]) == "UNAUTHORIZED"


def test_n11_child_assisted_stop_and_completion_reasons():
    f = Session("CHILD")
    f.speech(1, "PARENT")
    assert f.code("help.end", reason="PARENT_STOPPED") == "INVALID_INPUT"  # 부모는 이번 지원 대상이 아님
    assert f.code("help.end", reason="CHILD_COMPLETED") == "INVALID_INPUT"
    stopped = f.send("help.end", reason="CHILD_STOPPED")
    assert stopped.data.snapshot.end_reason is EndReason.CHILD_STOPPED
    assert ("observation.stop", "CHILD") in f.effects(stopped)
    assert f.code("speech.final", speaker_role="PARENT", utterance_id="t9", utterance_seq=9) == "NO_CONSENT"

    g = Session("CHILD")
    g.speech(1, "PARENT")
    done = g.send("help.end", reason="PARENT_COMPLETED")
    assert done.data.snapshot.end_reason is EndReason.PARENT_COMPLETED
    assert ("help.purge", "SERVER") in g.effects(done)


def test_n11_link_approval_fixes_next_help_target():
    f = Session("PARENT")
    f.speech(1, "CHILD")
    f.send("help.end", reason="CHILD_COMPLETED")
    first = f.s.snapshot.help_id
    assert f.code("help.link_approved", assisted_role="NOBODY") == "INVALID_INPUT"
    f.send("help.link_approved", assisted_role="CHILD")
    s = f.s.snapshot
    assert (s.assisted_role, s.help_state, s.event_seq) == ("CHILD", "LISTENING", 1)
    assert s.help_id != first and s.projection_epoch is None  # 새 도움은 새 캡처부터


# --- N-21 · N-22 · N-13 · N-23 ----------------------------------------------------


def view(snap, actor, arrival=ArrivalState.NONE):
    return project_role_view(RoleViewInput(snap, actor.role, arrival, True), actor=actor,
                             latest_help_id=snap.help_id, trace_id=TRACE)


def test_n21_child_assisted_child_gets_guide_view_parent_gets_minimal():
    snap = snapshot(**CHILD_HELP)
    c, p = view(snap, CHILD).data, view(snap, PARENT).data
    assert c.child_view is None and c.parent_view.goal.goal_id == "CLOCK_ALARM"
    assert c.parent_view.instruction.target_key == "settings.display"
    assert p.parent_view is None and set(vars(p.child_view)) == {"call_state", "can_complete", "arrival_indicator"}
    ended = snapshot(help_state=HelpState.ENDED, end_reason=EndReason.PARENT_COMPLETED, **CHILD_HELP)
    pv = view(ended, CHILD).data.parent_view
    assert (pv.status, pv.completion_message_key) == (ParentStatus.COMPLETED, "help.completed")
    stopped = snapshot(help_state=HelpState.ENDED, end_reason=EndReason.CHILD_STOPPED, **CHILD_HELP)
    assert view(stopped, CHILD).data.parent_view.status is ParentStatus.STOPPED


@pytest.mark.parametrize(("command", "actor", "expected"), [
    (HelpCommand.COMPLETE, PARENT, "PARENT_COMPLETED"),
    (HelpCommand.STOP, CHILD, "CHILD_STOPPED"),
    (HelpCommand.COMPLETE, CHILD, "UNAUTHORIZED"),
    (HelpCommand.STOP, PARENT, "UNAUTHORIZED"),
])
def test_n22_child_assisted_completion_by_parent_stop_by_child(command, actor, expected):
    r = resolve_arrival_or_completion(
        CompletionInput(None, command, "help-1", 1, ActorRole(actor.role.value), snapshot(**CHILD_HELP)),
        actor=actor, ledger=CompletionLedger(), trace_id=TRACE)
    got = r.data.end_reason.value if r.status == "success" else r.diagnostics.terminal_error.code
    assert got == expected


def test_n13_child_assisted_only_child_reobserves():
    store = InMemoryHelpStore()
    store.put_snapshot(snapshot(**CHILD_HELP))
    pairs = StaticPairDirectory({"pair-fixture": {"user-parent": Role.PARENT, "user-child": Role.CHILD}})
    for actor, expected in ((CHILD, True), (PARENT, False)):
        r = reconcile_reconnect(ReconnectRequest("pair-fixture", "help-1", actor.role, 10, "r"),
                                actor=actor, pairs=pairs, store=store, trace_id=TRACE)
        assert r.data.must_reobserve is expected


@pytest.mark.parametrize(("reason", "role", "ok", "requires"), [
    (EndReason.PARENT_COMPLETED, ActorRole.PARENT, True, False),
    (EndReason.CHILD_STOPPED, ActorRole.CHILD, True, True),
    (EndReason.PARENT_COMPLETED, ActorRole.CHILD, False, None),
    (EndReason.CHILD_STOPPED, ActorRole.PARENT, False, None),
])
def test_n23_reason_names_the_role_that_ended(reason, role, ok, requires):
    store = InMemoryHelpStore()
    snap = post_call(**CHILD_HELP)
    store.put_snapshot(snap)
    stores = [store, CompletionLedger()] + [ScopedBuffer(s) for s in (
        CleanupScope.NAVIGATION_TRAIL, CleanupScope.INFERENCE_RESULTS, CleanupScope.PENDING_UTTERANCES)]
    r = purge_help_and_restart(PurgeInput("help-1", reason, False, role, True), pair_id="pair-fixture",
                               current=snap, stores=stores, tombstones=TombstoneRegistry(),
                               events=RecordingEventSink(), trace_id=TRACE)
    assert (r.status == "success") is ok
    if ok:
        assert r.data.resume_requires_consent is requires


# --- 클라이언트 참조 구현 ------------------------------------------------------------


def test_client_refs_follow_assisted_device():
    child = ClientHelpCleanup(Role.CHILD, assisted_role=Role.CHILD)
    parent = ClientHelpCleanup(Role.PARENT, assisted_role=Role.CHILD)
    for app in (child, parent):
        app.start_help("help-1")
    assert LocalAction.CLEAR_OVERLAY in child.parent_stop_pressed()  # 지원 대상(자녀)의 안내 끄기
    assert parent.parent_stop_pressed() == []
    child.start_help("help-1")
    ended = ControlEvent(1, "help.ended", "end-1", "pair", "call", "help-1",
                         {"end_event_id": "end-1", "reason": "PARENT_COMPLETED"})
    assert LocalAction.SHOW_COMPLETION in child.on_help_ended(ended)
    assert parent.on_help_ended(ended) == [LocalAction.CLEAR_COMPLETE_BUTTON, LocalAction.CLEAR_ARRIVAL_NOTICE]

    gate_child = ClientResumeGate(Role.CHILD, assisted_role=Role.CHILD)
    gate_parent = ClientResumeGate(Role.PARENT, assisted_role=Role.CHILD)
    store = InMemoryHelpStore()
    store.put_snapshot(snapshot(**CHILD_HELP))
    pairs = StaticPairDirectory({"pair-fixture": {"user-parent": Role.PARENT, "user-child": Role.CHILD}})
    data = reconcile_reconnect(ReconnectRequest("pair-fixture", "help-1", Role.CHILD, 10, "r"),
                               actor=CHILD, pairs=pairs, store=store, trace_id=TRACE).data
    assert ClientAction.CLEAR_OVERLAY in gate_child.apply_reconnect(data)
    assert ClientAction.CLEAR_OVERLAY not in gate_parent.apply_reconnect(data)
