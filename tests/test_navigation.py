import unittest
from dataclasses import replace

from server.session.contracts import Basis, Run
from server.session.navigation import (
    InstructionTrace, NavigationCatalog, NavigationTrail, ScreenRef,
    Transition, record_verified_navigation,
)


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = NavigationCatalog(
            {"settings_home": "SETTINGS", "settings_display": "SETTINGS",
             "clock_alarm_list": "CLOCK", "unknown": "CLOCK"},
            {"settings.display"}, {"stable_change", "title_match"},
            reversible_pairs={("settings_home", "settings_display")},
        )
        self.token = Basis(pair_id="pair", call_id="call", help_id="help",
                           consent_revision=1, mode_revision=1, goal_revision=0,
                           instruction_revision=1, projection_epoch="epoch", screen_revision=1,
                           event_seq=1)
        self.trail = NavigationTrail("help")
        self.start = ScreenRef("settings_home", "epoch", 1)
        self.end = ScreenRef("settings_display", "epoch", 2)
        self.trace = InstructionTrace("ins-1", 1, "settings.display", 1, 1,
                                      ("title_match",), "VISIBLE", "location")

    def invoke(self, *, locked=None, **kwargs):
        run = Run(locked or self.token, source="FIXTURE")
        args = dict(run=run, current_basis=self.token, help_id="help", goal_revision=0,
                    trail=self.trail, observation_health="ACTIVE", catalog=self.catalog)
        result = record_verified_navigation(**(args | kwargs))
        self.assertEqual(result.diagnostics.source, "FIXTURE")
        self.assertEqual(result.diagnostics.basis, run.basis)
        if result.status == "success":
            self.trail = result.data.trail
            self.assertEqual(result.data.retained_count, len(self.trail.records))
        else:
            self.assertIsNone(result.data)
            self.assertIsNone(result.outcome)
        return result

    def observe(self, before, after, codes=("stable_change",), reversible=True):
        self.token = replace(self.token, event_seq=self.token.event_seq + 1,
                             screen_revision=after.screen_revision, projection_epoch=after.projection_epoch)
        return self.invoke(observed_transition=Transition(before, after, codes, reversible))

    def test_12_1_confirmed_transition_and_12_3_no_meaningful_change(self):
        self.assertEqual(self.observe(None, self.start).outcome, "unmodified")
        result = self.observe(self.start, self.end)
        self.assertEqual(result.outcome, "edge_recorded")
        edge, = self.trail.searchable_entries()
        self.assertEqual((edge.from_screen, edge.to_screen), (self.start, self.end))
        self.assertEqual(edge.evidence_codes, ("stable_change",))
        self.assertTrue(edge.reversible)
        self.assertEqual(result.data.recorded_edge.observed_seq, edge.observed_seq)
        before = self.trail
        self.assertEqual(self.invoke().outcome, "unmodified")
        self.assertIs(self.trail, before)
        self.assertEqual(self.observe(self.end, self.end).outcome, "unmodified")

    def test_12_2_and_12_6_gap_during_app_switch(self):
        self.observe(self.start, self.end)
        self.token = replace(self.token, event_seq=8)
        result = self.invoke(observation_health="GAP")
        self.assertEqual(result.outcome, "gap_recorded")
        self.assertTrue(result.data.trace_gap)
        self.assertEqual(self.trail.gap_after_seq, 8)
        self.assertFalse(self.trail.searchable_entries())
        clock = ScreenRef("clock_alarm_list", "epoch", 3)
        self.observe(self.end, clock)
        self.assertEqual(len(self.trail.entries), 1)
        self.assertFalse(self.trail.searchable_entries())
        self.assertEqual(self.observe(clock, replace(self.start, screen_revision=4)).outcome, "gap_recorded")

    def test_12_4_stale_and_missing_scopes(self):
        self.token = replace(self.token, screen_revision=2)
        transition = Transition(self.start, self.end, ("stable_change",))
        for changes in ({"help_id": "different"}, {"projection_epoch": "old"},
                        {"consent_revision": 0}, {"goal_revision": 9}):
            with self.subTest(changes=changes):
                result = self.invoke(locked=replace(self.token, **changes), observed_transition=transition)
                self.assertEqual(result.diagnostics.terminal_error.code, "STALE_BASIS")
        result = self.invoke(observed_transition=replace(transition, before=replace(self.start, projection_epoch="old")))
        self.assertEqual(result.diagnostics.terminal_error.code, "STALE_BASIS")
        self.token = replace(self.token, pair_id=None)
        self.assertEqual(self.invoke().diagnostics.terminal_error.code, "INVALID_INPUT")
        self.assertFalse(self.trail.records)

    def test_12_5_directive_keeps_only_approved_ids_and_screen_revisions(self):
        result = self.invoke(directive_record=self.trace, current_instruction_id="ins-1",
                             locked=replace(self.token, event_seq=0))
        self.assertEqual(result.outcome, "directive_recorded")
        trace, = self.trail.directives
        self.assertEqual(trace, replace(self.trace, projection_epoch=self.token.projection_epoch))
        self.assertFalse(self.trail.entries)
        earlier = self.trail
        self.assertEqual(self.invoke(directive_record=self.trace, current_instruction_id="ins-1").outcome,
                         "unmodified")
        for changes in ({"target_id": "010-0000-0000"}, {"evidence_codes": ("private@example.com",)},
                        {"instruction_revision": 0}, {"current_screen_revision": 9}):
            self.assertEqual(self.invoke(directive_record=replace(self.trace, **changes),
                                         current_instruction_id="ins-1").status, "error")
        self.assertIs(earlier, self.trail)

    def test_unproven_or_cross_app_transition_cannot_create_back_route(self):
        for before, after, codes in (
            (None, self.start, ()),
            (self.start, self.end, ()),
            (self.end, ScreenRef("clock_alarm_list", "epoch", 3), ("stable_change",)),
            (ScreenRef("clock_alarm_list", "epoch", 3), ScreenRef("unknown", "epoch", 4), ("stable_change",)),
        ):
            self.observe(before, after, codes)
            self.assertFalse(self.trail.entries)
            self.assertFalse(self.trail.searchable_entries())
        self.assertNotIn("unknown", repr(self.trail))

    def test_reverse_must_be_verified_for_the_observed_direction(self):
        self.observe(self.start, self.end, reversible=False)
        self.assertFalse(self.trail.entries[-1].reversible)
        self.observe(self.end, replace(self.start, screen_revision=3), reversible=True)
        self.assertFalse(self.trail.entries[-1].reversible)

    def test_control_change_on_same_screen_preserves_observed_navigation(self):
        self.observe(self.start, self.end)
        original_edges = self.trail.searchable_entries()
        changed = replace(self.end, screen_revision=3)
        result = self.observe(self.end, changed)
        self.assertEqual(result.outcome, "unmodified")
        self.assertIsNone(result.data.recorded_edge)
        self.assertIsNone(self.trail.gap_after_seq)
        self.assertEqual(self.trail.anchor, changed)
        self.assertEqual(self.trail.searchable_entries(), original_edges)
        result = self.observe(changed, replace(self.start, screen_revision=4))
        self.assertEqual(result.outcome, "edge_recorded")
        self.assertEqual(self.trail.searchable_entries()[1:], original_edges)

    def test_gap_without_current_screen_survives_retention_window(self):
        self.token = replace(self.token, projection_epoch=None, screen_revision=None)
        accepted = replace(self.trace, outcome="ACCEPTED", target_id=None,
                           current_screen_revision=None, origin_screen_revision=None,
                           evidence_codes=(), kind="instruction")
        self.assertEqual(self.invoke(directive_record=accepted, current_instruction_id="ins-1",
                                     observation_health="NOT_GRANTED").outcome, "directive_recorded")
        self.assertIsNone(self.trail.gap_after_seq)
        self.token = replace(self.token, event_seq=2)
        self.assertEqual(self.invoke(observation_health="GAP").outcome, "gap_recorded")
        for revision in range(2, 68):
            self.token = replace(self.token, event_seq=revision + 2, instruction_revision=revision)
            accepted = replace(accepted, instruction_id=f"ins-{revision}", instruction_revision=revision)
            self.invoke(directive_record=accepted, current_instruction_id=accepted.instruction_id,
                        observation_health="GAP")
        self.assertEqual((len(self.trail.records), len(self.trail.directives)), (60, 60))
        self.assertTrue(self.trail.history_truncated)
        self.observe(self.start, self.end)
        self.assertFalse(self.trail.entries)

    def test_search_uses_latest_twenty_contiguous_transitions(self):
        before = self.start
        for revision in range(2, 75):
            after = ScreenRef("settings_home" if revision % 2 else "settings_display", "epoch", revision)
            self.observe(before, after)
            before = after
        self.assertEqual(len(self.trail.records), 60)
        candidates = self.trail.searchable_entries()
        self.assertEqual([edge.to_screen.screen_revision for edge in candidates], list(range(74, 54, -1)))
        self.token = replace(self.token, event_seq=100)
        self.invoke(observation_health="PROTECTED")
        self.observe(None, replace(self.start, screen_revision=75))
        self.assertFalse(self.trail.searchable_entries())

    def test_unknown_payload_is_rejected_atomically(self):
        self.token = replace(self.token, screen_revision=2)
        bad = replace(self.trace, current_screen_revision=2, evidence_codes=("OCR private",))
        result = self.invoke(observed_transition=Transition(self.start, self.end, ("stable_change",)),
                             directive_record=bad, current_instruction_id="ins-1")
        self.assertEqual(result.status, "error")
        self.assertFalse(self.trail.records)
        self.assertNotIn("OCR private", repr(result))

    def test_location_and_assessment_changes_remain_distinct(self):
        unknown = replace(self.trace, target_id=None, evidence_codes=(), outcome="UNKNOWN")
        for trace in (self.trace, unknown, replace(unknown, kind="assessment"), self.trace):
            self.token = replace(self.token, event_seq=self.token.event_seq + 1)
            self.assertEqual(self.invoke(directive_record=trace, current_instruction_id="ins-1").outcome,
                             "directive_recorded")
        self.assertEqual([r.kind for r in self.trail.directives],
                         ["location", "location", "assessment", "location"])

    def test_new_capture_requires_fresh_location_evidence(self):
        self.invoke(directive_record=self.trace, current_instruction_id="ins-1")
        self.token = replace(self.token, event_seq=2)
        self.invoke(observation_health="GAP")
        self.token = replace(self.token, projection_epoch="new", event_seq=3)
        self.observe(None, replace(self.start, projection_epoch="new"))
        result = self.invoke(directive_record=self.trace, current_instruction_id="ins-1")
        self.assertEqual(result.outcome, "directive_recorded")
        self.assertEqual([r.projection_epoch for r in self.trail.directives], ["epoch", "new"])

    def test_unobservable_screen_revision_is_only_a_high_water_mark(self):
        for health in ("GAP", "PROTECTED", "STOPPED", "NOT_GRANTED"):
            for kind, outcome in (("instruction", "ACCEPTED"), ("location", "UNKNOWN"),
                                  ("assessment", "UNKNOWN")):
                with self.subTest(health=health, kind=kind):
                    self.trail = NavigationTrail("help")
                    trace = replace(self.trace, kind=kind, outcome=outcome, target_id=None,
                                    evidence_codes=(), current_screen_revision=None)
                    result = self.invoke(directive_record=trace, current_instruction_id="ins-1",
                                         observation_health=health)
                    self.assertEqual(result.outcome, "directive_recorded")
                    self.assertIsNone(self.trail.directives[-1].current_screen_revision)
                    result = self.invoke(directive_record=replace(trace, current_screen_revision=1),
                                         current_instruction_id="ins-1", observation_health=health)
                    self.assertEqual(result.diagnostics.terminal_error.code, "STALE_BASIS")
