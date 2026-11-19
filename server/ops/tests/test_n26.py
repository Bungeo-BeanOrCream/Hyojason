"""B-08 GPU·모델 준비, 크레딧 정책, V-08 증거 회귀."""

from dataclasses import replace
from unittest import TestCase

from server.agent._execution import ExecutionContext
from server.agent.infer_zoro_choice import InferZoroChoice
from server.contracts import Basis, ResultRef
from server.ops.runtime_smoke import collect_smoke_checks
from server.ops.track_model_runtime_and_budget import (
    MODEL_QUESTIONS,
    DecisionCheck,
    TrackModelRuntimeAndBudget,
    TrackModelRuntimeAndBudgetInput,
    v08_evidence,
)


class TestN26(TestCase):
    def setUp(self):
        self.context = ExecutionContext(trace_id="v08-fixture", source="LIVE")
        self.basis = Basis(event_seq=11)
        self.node = TrackModelRuntimeAndBudget()
        self.checks = tuple(
            DecisionCheck(
                question_kind=question,
                model_revision="zoro-rev1",
                succeeded=True,
                latency_ms=121,
                inference_source="LIVE",
                probability=0.93,
                result_ref=ResultRef(
                    trace_id="v08-fixture", node_id="N-14", run_id=f"run-{question}"
                ),
            )
            for question in MODEL_QUESTIONS
        )
        self.request = TrackModelRuntimeAndBudgetInput(
            model_revision="zoro-rev1",
            gpu_sku="L40S",
            spend_cents=3000,
            smoke_results=self.checks,
        )

    def run_node(self, request):
        return self.node.run(request, execution=self.context, basis=self.basis)

    def test_26_1_all_five_live_checks_and_diagnostic_scope(self):
        result = self.run_node(self.request)
        self.assertEqual((result.status, result.outcome), ("success", "runtime_ready"))
        self.assertTrue(result.data.model_ready)
        self.assertEqual(result.data.smoke_passes, 5)
        self.assertEqual(result.data.model_revision, "zoro-rev1")
        self.assertEqual(result.diagnostics.basis, self.basis)
        self.assertEqual(
            result.diagnostics.input_refs,
            tuple(check.result_ref for check in self.checks),
        )

    def test_26_2_B08_4_credit_boundaries(self):
        for spend, state, status in (
            (6999, "within_budget", "success"),
            (7000, "hold", "partial"),
            (9999, "hold", "partial"),
            (10_000, "exhausted", "partial"),
        ):
            with self.subTest(spend=spend):
                result = self.run_node(replace(self.request, spend_cents=spend))
                self.assertEqual(
                    (result.status, result.data.budget_state), (status, state)
                )
                self.assertTrue(result.data.model_ready)
                if status == "partial":
                    self.assertEqual(result.outcome, "budget_hold")

        result = self.run_node(replace(self.request, spend_cents=10_001))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.diagnostics.terminal_error.code, "CREDIT_CAP_EXCEEDED")
        self.assertIsNone(result.data)

    def test_B08_4_stop_before_call_when_experiments_on_hold(self):
        def unexpected_call(_request):
            self.fail("새 GPU 실험이 시작됨")

        for spend in (7000, 10_000, 10_001):
            with self.subTest(spend=spend):
                request = replace(self.request, spend_cents=spend, smoke_results=())
                collected = collect_smoke_checks(
                    request, infer=unexpected_call, execution=self.context
                )
                self.assertEqual(collected.smoke_results, ())

    def test_26_3_missing_fields_and_wrong_choice_prevent_ready(self):
        for checks, terminal in (
            (self.checks[:-1], "DEPENDENCY_UNAVAILABLE"),
            (
                (*self.checks[:-1], replace(self.checks[-1], probability=None)),
                "DEPENDENCY_UNAVAILABLE",
            ),
            (
                (*self.checks[:-1], replace(self.checks[-1], succeeded=False)),
                "UNVERIFIED_MODEL_CHOICE",
            ),
        ):
            with self.subTest(code=terminal):
                result = self.run_node(replace(self.request, smoke_results=checks))
                self.assertEqual(result.status, "error")
                self.assertIsNone(result.outcome)
                self.assertIsNone(result.data)
                self.assertEqual(result.diagnostics.terminal_error.code, terminal)

    def test_26_4_mixed_revision_is_an_error(self):
        result = self.run_node(
            replace(
                self.request,
                smoke_results=(
                    *self.checks[:-1],
                    replace(self.checks[-1], model_revision="old"),
                ),
            )
        )
        self.assertEqual(result.diagnostics.terminal_error.code, "STALE_BASIS")
        self.assertEqual(result.diagnostics.basis, self.basis)
        self.assertTrue(result.diagnostics.run_id)
        self.assertIsNone(result.data)

    def test_B08_2_live_timeout_is_retained_with_fixture_result(self):
        timeout = replace(
            self.checks[0], succeeded=False, probability=None, request_status="TIMEOUT"
        )
        fallback = replace(self.checks[-1], inference_source="FIXTURE")
        request = replace(
            self.request, smoke_results=(timeout, *self.checks[1:-1], fallback)
        )
        result = self.run_node(request)
        self.assertEqual(result.diagnostics.terminal_error.code, "TIMEOUT")
        self.assertEqual(result.diagnostics.source, "FIXTURE")
        self.assertEqual(result.diagnostics.steps["smoke_goal"].status, "failed")
        self.assertEqual(result.diagnostics.steps["smoke_arrival"].status, "skipped")
        rows = v08_evidence(request, execution=self.context)["observed_runs"]
        timeout_row, fallback_row = rows[0], rows[-2]
        self.assertEqual(timeout_row["actual"]["request_status"], "TIMEOUT")
        self.assertEqual(timeout_row["model_source"], "LIVE")
        self.assertEqual(fallback_row["source"], "FIXTURE")
        self.assertEqual(fallback_row["model_source"], "FIXTURE")

    def test_B08_3_fixture_context_cannot_promote_live_attribution(self):
        fixture = replace(self.context, source="FIXTURE")
        result = self.node.run(self.request, execution=fixture)
        self.assertIsNone(result.data)
        self.assertEqual(result.diagnostics.source, "FIXTURE")
        rows = v08_evidence(self.request, execution=fixture)["observed_runs"]
        self.assertTrue(all(row["source"] == "FIXTURE" for row in rows))

    def test_B08_3_budget_evidence_does_not_invent_pause_confirmation(self):
        hold = replace(self.request, spend_cents=7000)
        ledger = v08_evidence(hold, execution=self.context)["observed_runs"][-1]
        self.assertEqual(ledger["actual"], {"credits_cents": 7000})
        confirmed = replace(hold, experiments_paused_at_70=True)
        ledger = v08_evidence(confirmed, execution=self.context)["observed_runs"][-1]
        self.assertEqual(
            ledger["actual"], {"credits_cents": 7000, "experiments_paused_at_70": True}
        )

    def test_bad_telemetry_and_duplicate_question_are_rejected(self):
        for bad in (
            replace(self.request, spend_cents=-1),
            replace(self.request, spend_cents=True),
            replace(self.request, credit_cents=10_001),
            replace(self.request, smoke_results=(*self.checks, self.checks[0])),
            replace(
                self.request,
                smoke_results=(replace(self.checks[0], probability=float("nan")),),
            ),
        ):
            with self.subTest(bad=bad):
                self.assertEqual(
                    self.run_node(bad).diagnostics.terminal_error.code, "INVALID_INPUT"
                )

    def test_v08_case_ids_and_fields_match_d_report(self):
        rows = v08_evidence(self.request, execution=self.context, basis=self.basis)[
            "observed_runs"
        ]
        self.assertEqual(
            [row["case_id"] for row in rows],
            [f"V08-{question}" for question in MODEL_QUESTIONS] + ["V08-budget"],
        )
        self.assertEqual(
            set(rows[0]["actual"]),
            {
                "question_type",
                "gpu",
                "probability",
                "latency_ms",
                "choice_verified",
                "request_status",
            },
        )
        self.assertTrue(all(row["basis"]["event_seq"] == 11 for row in rows))

    def test_smoke_runs_five_n14_questions_and_preserves_fixture_source(self):
        seen = []

        def transport(payload, timeout):
            seen.append(payload)
            criteria = payload["request"]["questions"]["decision"]["criteria"]
            picks = {
                "CLOCK_ALARM",
                "tap:settings.display",
                "CORRECT",
                "tap:clock.add_alarm",
                "ARRIVED",
            }
            (pick,) = picks.intersection(criteria)
            return {
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": pick,
                        "probabilities": {
                            key: 0.95 if key == pick else 0.05 / (len(criteria) - 1)
                            for key in criteria
                        },
                    }
                }
            }

        model = InferZoroChoice(model_revision="zoro-rev1", transport=transport)
        collected = collect_smoke_checks(
            replace(self.request, smoke_results=()),
            infer=model.run,
            execution=self.context,
        )
        self.assertEqual(len(seen), 5)
        self.assertTrue(all(check.succeeded for check in collected.smoke_results))
        self.assertTrue(
            all(
                check.inference_source == "FIXTURE" for check in collected.smoke_results
            )
        )
        self.assertEqual(self.run_node(collected).status, "error")
