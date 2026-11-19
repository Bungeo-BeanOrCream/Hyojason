"""JSON boundary checks using synthetic session results and fixture provenance."""

import json
from types import MappingProxyType
from unittest import TestCase

from server.session.contracts import Basis, ResultRef, Run
from server.session.help_state import Goal, HelpSnapshot, Reduction


def round_trip(result):
    return json.loads(json.dumps(result.to_dict(), allow_nan=False))


class WireContractTests(TestCase):
    def test_error_keeps_all_basis_keys_and_null_data(self):
        run = Run(Basis(help_id="fixture-help"), source="FIXTURE", trace_id="trace")
        wire = round_trip(run.error("N-11", "STALE_BASIS"))
        self.assertEqual(set(wire), {"status", "outcome", "data", "diagnostics"})
        self.assertEqual((wire["status"], wire["outcome"], wire["data"]), ("error", None, None))
        diagnostic = wire["diagnostics"]
        self.assertEqual((diagnostic["source"], diagnostic["input_refs"]), ("FIXTURE", []))
        self.assertEqual(diagnostic["basis"], {
            "pair_id": None, "call_id": None, "help_id": "fixture-help", "event_seq": None,
            "consent_revision": None, "goal_revision": None, "mode_revision": None,
            "instruction_revision": None, "projection_epoch": None, "screen_revision": None,
            "utterance_seq": None,
        })
        self.assertEqual(diagnostic["terminal_error"], {
            "code": "STALE_BASIS", "step": "validate", "retryable": False,
        })
        self.assertEqual(diagnostic["steps"]["validate"], {"status": "failed", "code": "STALE_BASIS"})

    def test_success_exports_frozen_goal_slots_and_result_references(self):
        goal = Goal("CLOCK_ALARM", 1, MappingProxyType({"hour": 6}), "fixture-turn")
        snapshot = HelpSnapshot("fixture-pair", "fixture-call", "fixture-help", goal=goal, goal_revision=1)
        run = Run(snapshot.basis, source="FIXTURE", input_refs=(ResultRef("trace", "N-15", "draft"),))
        wire = round_trip(run.success("N-11", "state_applied", Reduction(snapshot, (), 1)))
        self.assertEqual(wire["data"]["snapshot"]["goal"]["approved_slots"], {"hour": 6})
        self.assertEqual(wire["data"]["side_effects"], [])
        self.assertEqual(wire["diagnostics"]["input_refs"], [
            {"trace_id": "trace", "node_id": "N-15", "run_id": "draft"},
        ])
        self.assertIsNone(wire["diagnostics"]["terminal_error"])
