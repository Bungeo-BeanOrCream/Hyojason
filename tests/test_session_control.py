"""Integration fixtures for the session server. No RTC, AVD or model is invoked."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from itertools import count
from unittest import TestCase

from server.session.contracts import Basis, Run
from server.session.control import SessionControl
from server.session.help_state import Actor, Event
from server.session.navigation import NavigationCatalog


class SessionFlow(TestCase):
    def setUp(self):
        self.ids = count(1)
        self.catalog = NavigationCatalog(
            screen_apps={"settings_home": "settings", "settings_display": "settings", "settings_sound": "settings"},
            target_ids={"settings.display"}, evidence_codes={"STABLE_CONTROLS", "VISIBLE_LABEL"},
            reversible_pairs={("settings_home", "settings_sound"), ("settings_home", "settings_display")},
        )
        self.control = SessionControl(
            "pair", "call", "help", catalog=self.catalog,
            approved_slots={"SET_FONT": lambda slots: slots == {}}, source="FIXTURE",
        )
        self.emit("call.connected")
        for role in ("PARENT", "CHILD"):
            self.emit("consent.updated", role, purpose="analysis", enabled=True)
        self.emit("projection.started", "PARENT", projection_epoch="capture")

    def apply(self, event_type, role="SERVER", *, basis=None, event_id=None, **payload):
        basis = basis or self.control.snapshot.basis
        event = Event(event_type, event_id or f"e{next(self.ids)}", basis.pair_id, basis.call_id,
                      basis.help_id, payload)
        return self.control.apply(event, Actor(role, basis.pair_id, basis.call_id), basis)

    def emit(self, event_type, role="SERVER", **kwargs):
        update = self.apply(event_type, role, **kwargs)
        self.assertEqual(update.state.status, "success", update.state.diagnostics)
        return update

    def screen(self, kind, revision, **extra):
        return self.emit(
            "screen.observed", "PARENT", projection_epoch="capture", screen_revision=revision,
            screen_kind=kind, target_ids=["settings.display"], evidence_codes=["STABLE_CONTROLS"],
            observation_health="ACTIVE", **extra,
        )

    def turn(self, seq):
        self.emit("speech.final", speaker_role="CHILD", utterance_id=f"turn{seq}", utterance_seq=seq,
                  text="fixture-only private utterance")
        return self.emit("instruction.drafted", source_utterance_id=f"turn{seq}", action="TAP",
                         target_key="settings.display", supersedes_previous=True)

    def test_wrong_menu_preserves_origin_and_replacement_rejects_late_location(self):
        self.screen("settings_home", 1)
        instruction = self.turn(1)
        original = self.control.snapshot.basis
        self.assertEqual(instruction.navigation.outcome, "directive_recorded")
        self.assertEqual(instruction.navigation.diagnostics.input_refs, (instruction.state.diagnostics.ref,))
        self.assertEqual(instruction.navigation.diagnostics.source, "FIXTURE")
        self.assertEqual(instruction.navigation.diagnostics.basis, original)
        self.assertEqual(self.control.snapshot.trail_cursor, original.event_seq)
        self.assertNotEqual(instruction.navigation.diagnostics.run_id, instruction.state.diagnostics.run_id)

        self.screen("settings_sound", 2, from_screen_revision=1, reversible=True)
        assessed = self.emit("action.assessed", verdict="WRONG", instruction_revision=1,
                             from_screen_revision=1, to_screen_revision=2,
                             evidence_codes=["STABLE_CONTROLS"], model_revision="fixture")
        trace = assessed.navigation.data.trail.directives[-1]
        self.assertEqual((trace.outcome, trace.origin_screen_revision, trace.current_screen_revision),
                         ("WRONG", 1, 2))
        edge, = self.control.trail.searchable_entries()
        self.assertEqual((edge.from_screen.screen_kind, edge.to_screen.screen_kind, edge.reversible),
                         ("settings_home", "settings_sound", True))
        wire_trail = assessed.navigation.to_dict()["data"]["trail"]
        self.assertEqual(set(wire_trail), {"entries", "directives", "gap_after_seq"})
        self.assertEqual(wire_trail["directives"][-1]["outcome"], "WRONG")
        self.turn(2)
        before = self.control.snapshot, self.control.trail
        late = self.apply("target.located", basis=original, location_kind="visible",
                          target_id="settings.display", anchor_screen_revision=1,
                          evidence_codes=["VISIBLE_LABEL"], model_revision="fixture")
        self.assertEqual(late.state.diagnostics.terminal_error.code, "STALE_BASIS")
        self.assertIsNone(late.navigation)
        self.assertEqual((self.control.snapshot, self.control.trail), before)
        self.assertNotIn("private utterance", repr((before, asdict(late.state.diagnostics))))

    def test_gap_handoff_and_completion_in_one_help(self):
        self.screen("settings_home", 1)
        self.turn(1)
        self.emit("goal.drafted", source_utterance_id="turn1", goal_id="SET_FONT", approved_slots={})
        self.emit("screen.observed", "PARENT", projection_epoch="capture", observation_health="GAP")
        self.screen("settings_sound", 2, from_screen_revision=1, reversible=True)
        self.assertEqual(self.control.trail.entries, ())
        self.assertIsNotNone(self.control.trail.gap_after_seq)
        self.assertEqual(len(self.control.trail.directives), 1)

        ended_call = self.emit("call.ended")
        self.assertEqual(ended_call.state.outcome, "call_ended_help_active")
        self.assertEqual((self.control.snapshot.help_id, self.control.snapshot.mode),
                         ("help", "POST_CALL_AGENT"))
        self.assertIsNone(ended_call.navigation)
        self.assertEqual(len(self.control.trail.directives), 1)
        self.emit("help.complete", "CHILD")
        self.assertEqual(self.control.trail.records, ())
        self.assertIsNone(self.control.trail.anchor)

    def test_concurrent_retries_publish_one_effect_and_history_entry(self):
        self.screen("settings_home", 1)
        self.emit("speech.final", speaker_role="CHILD", utterance_id="turn1", utterance_seq=1)
        basis = self.control.snapshot.basis

        def deliver(_):
            return self.apply("instruction.drafted", basis=basis, event_id="same-command",
                              source_utterance_id="turn1", action="TAP", target_key="settings.display",
                              supersedes_previous=True)

        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(deliver, range(12)))
        self.assertCountEqual([r.state.outcome for r in replies],
                              ["state_applied"] + ["duplicate_ignored"] * 11)
        self.assertEqual(sum(r.navigation is not None for r in replies), 1)
        self.assertEqual(len(self.control.trail.directives), 1)
        self.assertEqual(self.control.snapshot.event_seq, basis.event_seq + 1)

    def test_unverified_transition_cannot_finish_the_instruction(self):
        self.screen("settings_home", 1)
        self.turn(1)
        change = self.emit("screen.observed", "PARENT", projection_epoch="capture",
                           screen_revision=2, from_screen_revision=1, screen_kind="settings_display",
                           target_ids=[], evidence_codes=[], observation_health="ACTIVE")
        self.assertEqual(change.navigation.outcome, "gap_recorded")
        before = self.control.snapshot, self.control.trail
        result = self.apply("action.assessed", instruction_revision=1, verdict="CORRECT",
                            from_screen_revision=1, to_screen_revision=2,
                            evidence_codes=["STABLE_CONTROLS"], model_revision="fixture")
        self.assertEqual(result.state.status, "error")
        self.assertEqual((self.control.snapshot, self.control.trail), before)

    def test_verified_control_change_updates_trace_without_a_navigation_edge(self):
        self.screen("settings_display", 1)
        self.turn(1)
        same_screen = self.screen("settings_display", 2, from_screen_revision=1)
        self.assertEqual(same_screen.navigation.outcome, "unmodified")
        self.assertIsNone(self.control.trail.gap_after_seq)
        self.assertEqual(self.control.trail.anchor.screen_revision, 2)
        update = self.emit("action.assessed", verdict="CORRECT", instruction_revision=1,
                           from_screen_revision=1, to_screen_revision=2,
                           evidence_codes=["STABLE_CONTROLS"], model_revision="fixture")
        self.assertEqual(update.navigation.data.trail.directives[-1].outcome, "CORRECT")
        self.assertEqual(self.control.trail.searchable_entries(), ())
        self.assertIsNone(self.control.snapshot.instruction)

    def test_revoking_consent_purges_history_and_late_evidence_has_no_effect(self):
        self.screen("settings_home", 1)
        self.turn(1)
        old = self.control.snapshot.basis
        self.emit("consent.updated", "PARENT", purpose="analysis", enabled=False)
        self.assertEqual(self.control.trail.records, ())
        self.assertIsNone(self.control.trail.anchor)
        late = self.apply("target.located", basis=old, location_kind="visible",
                          target_id="settings.display", anchor_screen_revision=1,
                          evidence_codes=["VISIBLE_LABEL"], model_revision="fixture")
        self.assertEqual(late.state.status, "error")
        self.assertIsNone(late.navigation)
        self.assertIsNone(self.control.snapshot.screen)

    def test_observed_edge_can_span_confirmed_hangup_in_same_capture(self):
        self.screen("settings_home", 1)
        self.turn(1)
        self.emit("goal.drafted", source_utterance_id="turn1", goal_id="SET_FONT", approved_slots={})
        self.emit("call.ended")
        self.assertIsNone(self.control.snapshot.screen)
        observed = self.screen("settings_display", 2, from_screen_revision=1, reversible=True)
        self.assertEqual(observed.navigation.outcome, "edge_recorded")
        self.assertEqual(len(self.control.trail.searchable_entries()), 1)

    def test_fixture_provenance_propagates_to_later_updates(self):
        control = SessionControl("pair", "call", "help", catalog=self.catalog, approved_slots={})
        actor = Actor("SERVER", "pair", "call")
        rtc = Run(Basis(), source="FIXTURE").success("N-03", "connected", ())
        result = control.apply(Event("call.connected", "rtc", "pair", "call", "help", {}),
                               actor, control.snapshot.basis, inputs=(rtc,)).state
        self.assertEqual(result.diagnostics.source, "FIXTURE")
        self.assertEqual(result.diagnostics.trace_id, rtc.diagnostics.trace_id)
        self.assertEqual(result.diagnostics.input_refs, (rtc.diagnostics.ref,))
        result = control.apply(Event("consent.updated", "enable", "pair", "call", "help",
                                     {"purpose": "analysis", "enabled": True}),
                               Actor("PARENT", "pair", "call"), control.snapshot.basis).state
        self.assertEqual(result.diagnostics.source, "FIXTURE")
