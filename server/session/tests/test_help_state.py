"""N-11 fixture events from authenticated node boundaries."""

from dataclasses import replace
from itertools import count
from unittest import TestCase

from hyojason_session.common.enums import EndReason, HelpState
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import snapshot_problems
from hyojason_session.processing import Run
from hyojason_session.help_state import Actor, Event, HelpSession


class ReducerTests(TestCase):
    def setUp(self):
        self.session = HelpSession(
            "family", "call", "help",
            screen_apps={"settings_home": "settings", "settings_display": "settings", "clock_alarm_list": "clock"},
            target_ids={"settings.display"}, evidence_codes={"VISIBLE"},
            approved_slots={"SET_FONT": lambda slots: slots == {"steps": 1}},
        )
        self.ids = count()
        self.send("call.connected")
        for role in ("PARENT", "CHILD"):
            self.send("consent.updated", role, purpose="analysis", enabled=True)

    @property
    def basis(self):
        return self.session.snapshot.basis

    def apply(self, kind, role="SERVER", *, basis=None, event_id=None, **payload):
        basis = basis or self.basis
        event = Event(kind, event_id or str(next(self.ids)), basis.pair_id,
                      basis.call_id, basis.help_id, payload)
        run = Run(basis, source="FIXTURE", trace_id="demo")
        result = self.session.reduce_help_state(event, Actor(role, basis.pair_id, basis.call_id), run)
        self.assertEqual(result.diagnostics.basis, basis)
        self.assertEqual(result.diagnostics.run_id, run.run_id)
        return result

    def send(self, *args, **kwargs):
        result = self.apply(*args, **kwargs)
        self.assertEqual(result.status, "success", result.diagnostics)
        return result

    def reject(self, code, *args, **kwargs):
        before = self.session.snapshot
        result = self.apply(*args, **kwargs)
        self.assertEqual(result.status, "error")
        self.assertEqual((result.outcome, result.data), (None, None))
        self.assertEqual(result.diagnostics.terminal_error.code, code)
        self.assertEqual(self.session.snapshot, before)

    def speech(self, seq, role="CHILD"):
        return self.send("speech.final", utterance_id=f"turn-{seq}", utterance_seq=seq,
                         speaker_role=role, text="volatile fixture utterance")

    def draft(self, seq, kind="instruction", **opts):
        payload = ({"target_key": "settings.display", "action": "TAP", "supersedes_previous": True}
                   if kind == "instruction" else {"goal_id": "SET_FONT", "approved_slots": {"steps": 1}})
        return self.send(kind + ".drafted", source_utterance_id=f"turn-{seq}", **payload, **opts)

    def screen(self, revision=1, kind="settings_home", **extra):
        return self.send("screen.observed", "PARENT", projection_epoch="epoch", screen_revision=revision,
                         observation_health="ACTIVE", screen_kind=kind, target_ids=["settings.display"],
                         evidence_codes=["VISIBLE"], **extra)

    def location(self):
        return dict(location_kind="visible", target_id="settings.display", anchor_screen_revision=1,
                    evidence_codes=["VISIBLE"], model_revision="fixture")

    @staticmethod
    def effects(result):
        return [ref.kind for ref in result.data.side_effects]

    def test_turn_scopes_and_idempotency(self):
        self.speech(1)
        original = self.basis
        self.speech(2, "PARENT")
        first = self.draft(1, "goal", basis=original)
        second = self.draft(1, basis=original, event_id="instruction")
        self.assertEqual(self.effects(first).count("audio.evaluate") + self.effects(second).count("audio.evaluate"), 1)
        self.assertEqual(self.effects(second), ["instruction.accepted"])
        self.assertEqual((self.basis.goal_revision, self.basis.instruction_revision), (1, 1))
        for event_id in ("instruction", "another-id"):
            retry = self.draft(1, basis=original, event_id=event_id)
            self.assertEqual(retry.outcome, "duplicate_ignored")
            self.assertEqual(retry.data.side_effects, ())
            self.assertEqual(self.basis.event_seq, second.data.applied_event_seq)
        self.speech(3)
        self.draft(3, "goal")
        self.assertEqual(self.basis.goal_revision, 1)
        self.reject("STALE_BASIS", "goal.drafted", basis=original, source_utterance_id="turn-1",
                    goal_id="SET_FONT", approved_slots={"steps": 1})
        self.assertNotIn("volatile fixture", repr(self.session.snapshot))

    def test_replaced_instruction_and_hangup_invalidate_old_model_results(self):
        self.send("projection.started", "PARENT", projection_epoch="epoch")
        self.screen()
        self.speech(1)
        self.draft(1)
        old = self.basis
        self.speech(2)
        replaced = self.draft(2)
        self.assertEqual(self.effects(replaced), ["instruction.cleared", "instruction.accepted"])
        self.reject("STALE_BASIS", "target.located", basis=old, **self.location())
        self.draft(2, "goal")
        basis = self.basis
        self.send("call.reconnecting")
        self.assertEqual(self.session.snapshot.mode, "CALL_CHILD_LED")
        self.reject("STALE_BASIS", "target.located", basis=basis, **self.location())
        self.send("call.connected")
        self.reject("STALE_BASIS", "target.located", basis=basis, **self.location())
        self.send("target.located", **self.location())
        result = self.send("call.ended")
        self.assertEqual(result.outcome, "call_ended_help_active")
        self.assertIn("stt.close", self.effects(result))
        self.assertIn("screen.reobserve", self.effects(result))
        s = result.data.snapshot
        self.assertEqual((s.mode, s.help_id, s.help_state, s.mode_revision),
                         ("POST_CALL_AGENT", "help", "ACTIVE", 1))
        self.assertIsNone(s.instruction)
        self.assertIsNone(s.screen)
        self.reject("STALE_BASIS", "target.located", basis=basis, **self.location())

    def test_hangup_and_completion_in_either_order(self):
        self.speech(1)
        self.draft(1, "goal")
        basis = self.basis
        self.send("call.ended")
        complete = self.send("help.complete", "CHILD", basis=basis, event_id="done")
        self.assertIn("trail.clear", self.effects(complete))
        self.assertIsNone(complete.data.snapshot.goal)
        self.assertEqual(self.send("help.complete", "CHILD", basis=basis, event_id="done").outcome, "duplicate_ignored")
        self.reject("ALREADY_ENDED", "help.complete", "CHILD")
        self.setUp()
        self.speech(1)
        self.draft(1, "goal")
        self.send("help.complete", "CHILD")
        ended = self.send("call.ended")
        self.assertEqual(ended.data.snapshot.help_state, "ENDED")
        self.assertNotIn("screen.reobserve", self.effects(ended))

    def test_fresh_help_and_sibling_drafts_from_same_opening_turn(self):
        self.speech(1)
        self.draft(1, "goal")
        before_completion = self.basis
        self.send("help.complete", "CHILD")
        self.reject("STALE_BASIS", "speech.final", basis=before_completion,
                    speaker_role="CHILD", utterance_id="delayed", utterance_seq=2)
        self.speech(2)
        opening = self.basis
        result = self.draft(2, basis=opening, event_id="open")
        self.assertNotEqual(self.basis.help_id, opening.help_id)
        self.assertEqual(self.basis.event_seq, 1)
        self.assertNotIn("audio.evaluate", self.effects(result))
        self.assertEqual(self.draft(2, basis=opening, event_id="open").outcome, "duplicate_ignored")
        self.draft(2, "goal", basis=opening)
        self.assertEqual((self.basis.goal_revision, self.basis.instruction_revision), (1, 1))
        self.reject("STALE_BASIS", "help.complete", "CHILD", basis=opening)
        self.reject("UNAUTHORIZED", "call.ended", basis=replace(opening, call_id="foreign"))
        ended = self.send("call.ended", basis=before_completion)
        self.assertEqual(ended.outcome, "call_ended_help_active")
        self.assertNotEqual(ended.data.snapshot.help_id, before_completion.help_id)
        retry = self.send("call.ended", basis=replace(before_completion, help_id=None))
        self.assertEqual(retry.outcome, "duplicate_ignored")

    def test_shared_snapshot_projects_canonical_types_and_retains_end_reason(self):
        self.send("projection.started", "PARENT", projection_epoch="epoch")
        self.screen()
        self.speech(1)
        self.draft(1)
        self.draft(1, "goal")
        projected = self.session.snapshot.to_shared_snapshot()
        self.assertEqual(snapshot_problems(projected), [])
        self.assertIs(projected.help_state, HelpState.ACTIVE)
        self.assertEqual(to_jsonable(projected)["goal"]["approved_slots"], {"steps": "1"})
        self.assertNotIn("utterance_seq", to_jsonable(projected))
        self.send("help.complete", "CHILD")
        self.send("consent.updated", "CHILD", purpose="analysis", enabled=False)
        ended = self.session.snapshot.to_shared_snapshot()
        self.assertEqual(snapshot_problems(ended), [])
        self.assertIs(ended.end_reason, EndReason.CHILD_COMPLETED)

    def test_reconsent_and_ended_help_without_goal(self):
        self.speech(1)
        first = self.draft(1)
        self.send("help.stop", "PARENT")
        self.reject("NO_CONSENT", "speech.final", speaker_role="CHILD", utterance_seq=2, utterance_id="turn-2")
        self.send("consent.updated", "PARENT", purpose="analysis", enabled=True)
        self.reject("STALE_BASIS", "instruction.drafted", source_utterance_id="turn-1",
                    action="TAP", target_key=None, supersedes_previous=True)
        self.speech(2)
        reopened = self.draft(2)
        self.assertNotEqual(reopened.data.snapshot.help_id, first.data.snapshot.help_id)
        ended = self.send("call.ended")
        self.assertEqual((ended.data.snapshot.mode, ended.data.snapshot.help_state), ("CALL_CHILD_LED", "ENDED"))

    def test_authorization_and_input_rejection(self):
        self.reject("UNAUTHORIZED", "help.complete", "PARENT")
        self.reject("UNAUTHORIZED", "call.ended", "CHILD")
        self.reject("UNAUTHORIZED", "help.stop", "PARENT", basis=replace(self.basis, pair_id="foreign"))
        self.reject("INVALID_INPUT", "speech.final", speaker_role="CHILD", utterance_seq=True, utterance_id="bad")
        self.speech(1)
        self.reject("INVALID_INPUT", "instruction.drafted", source_utterance_id="turn-1", action="TAP",
                    target_key="raw fixture input", supersedes_previous=True)
        self.reject("INVALID_INPUT", "goal.drafted", source_utterance_id="turn-1",
                    goal_id="SET_FONT", approved_slots={"raw": "fixture text"})
        self.reject("STALE_BASIS", "help.complete", "CHILD", basis=replace(self.basis, goal_revision=123))

    def test_pre_gap_result_cannot_restore_a_protected_screen(self):
        self.send("projection.started", "PARENT", projection_epoch="epoch")
        self.screen()
        old = self.basis
        result = self.send("screen.observed", "PARENT", observation_health="ACTIVE",
                           screen_kind="protected")
        self.assertEqual(result.data.snapshot.observation_health, "PROTECTED")
        self.assertIsNone(result.data.snapshot.screen)
        self.reject("STALE_BASIS", "screen.observed", "PARENT", basis=old, screen_revision=1,
                    projection_epoch="epoch", screen_kind="settings_home", observation_health="ACTIVE",
                    target_ids=[], evidence_codes=["VISIBLE"])
        self.screen()
        revoked = self.send("screen.observed", "PARENT", observation_health="STOPPED")
        self.assertIn("trail.clear", self.effects(revoked))
        self.reject("STALE_BASIS", "projection.started", "PARENT", projection_epoch="epoch")

    def test_goal_arrival_requires_fresh_post_call_screen(self):
        self.send("projection.started", "PARENT", projection_epoch="epoch")
        self.screen()
        self.speech(1)
        self.draft(1, "goal")
        self.reject("NOT_APPLICABLE", "arrival.suspected", arrival_id="arr")
        self.send("call.ended")
        self.reject("CAPTURE_GAP", "arrival.suspected", arrival_id="arr")
        self.screen()
        self.send("arrival.suspected", arrival_id="arr")
        self.assertEqual(self.session.snapshot.help_state, "ARRIVAL_PENDING")
        self.send("arrival.revoked", arrival_id="arr")
        self.assertEqual(self.session.snapshot.help_state, "ACTIVE")
        self.send("arrival.suspected", arrival_id="arr-2")
        before_gap = self.basis
        self.send("screen.observed", "PARENT", observation_health="PROTECTED")
        self.reject("STALE_BASIS", "arrival.revoked", arrival_id="arr-2", basis=before_gap)
        self.reject("STALE_BASIS", "arrival.revoked", arrival_id="arr")
        self.send("arrival.revoked", arrival_id="arr-2")
        self.assertEqual(self.session.snapshot.help_state, "ACTIVE")

    def test_external_app_and_unknown_screen_break_evidence_chain(self):
        self.send("projection.started", "PARENT", projection_epoch="epoch")
        self.screen()
        self.speech(1)
        self.draft(1)
        self.screen(2, "clock_alarm_list", from_screen_revision=1)
        self.reject("STALE_BASIS", "action.assessed", verdict="CORRECT", instruction_revision=1,
                    from_screen_revision=1, to_screen_revision=2, evidence_codes=["VISIBLE"])
        self.screen(3, "unregistered-kind", from_screen_revision=2)
        self.assertIsNone(self.session.snapshot.screen)
        self.assertEqual(self.session.snapshot.observation_health, "GAP")
        self.assertNotIn("unregistered-kind", repr(self.session.snapshot))

    def test_verified_control_change_within_a_screen_can_close_instruction(self):
        self.send("projection.started", "PARENT", projection_epoch="epoch")
        self.screen()
        self.speech(1)
        self.draft(1)
        self.screen()  # A layout-only observation keeps the semantic revision.
        self.reject("STALE_BASIS", "action.assessed", verdict="CORRECT", instruction_revision=1,
                    from_screen_revision=1, to_screen_revision=1, evidence_codes=["VISIBLE"])
        self.screen(2, "settings_home", from_screen_revision=1)
        self.send("action.assessed", verdict="CORRECT", instruction_revision=1,
                  from_screen_revision=1, to_screen_revision=2, evidence_codes=["VISIBLE"])
        self.assertIsNone(self.session.snapshot.instruction)
