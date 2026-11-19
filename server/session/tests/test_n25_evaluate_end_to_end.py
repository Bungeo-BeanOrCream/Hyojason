"""N-25 evidence and benchmark fixtures."""

from dataclasses import replace

from hyojason_session.common.enums import Mode, Source
from hyojason_session.common.result import Basis, ResultRef, Status
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.demo_evaluation import (
    PRIVACY_CHECKS,
    REGRESSION_CHECKS,
    REHEARSAL_CHECKS,
    TRANSITION_CHECKS,
    RunEvidence,
    ScenarioCase,
    Verdict,
    evaluate_end_to_end,
)


BASIS = Basis(help_id="help-1", mode_revision=2, instruction_revision=3, screen_revision=18)
REF = ResultRef(trace_id="upstream", node_id="N-24", run_id="upstream-run")


def evaluate(cases, evidence, source=Source.LIVE):
    return evaluate_end_to_end(
        cases, evidence, 3, tuple(Mode), basis=BASIS, trace_id="evaluation-trace",
        source=source, input_refs=(REF,),
    )


def run(case, actual, *, provenance=None, **kwargs):
    source = provenance or case.source
    defaults = dict(
        basis=BASIS, screen_source=source, input_source=source, model_source=source,
        device_revision="android15", app_revision="settings-v1", profile_revision="profile-2",
        model_revision="zoro1-rev42",
    )
    return RunEvidence(
        case.case_id, kwargs.pop("attempt", 1), f"trace-{case.case_id}", source,
        actual, **(defaults | kwargs),
    )


def test_25_1_rehearsals_keep_first_failure_and_report_four_of_five_for_each():
    cases, evidence = [], []
    for scenario, checks in REHEARSAL_CHECKS.items():
        for index in range(1, 6):
            case = ScenarioCase(f"{scenario}{index}", "V-01", {}, scenario)
            actual = dict(checks)
            if index == 1:
                actual["parent_acted"] = False
                evidence.append(run(case, dict(checks), attempt=2))
            cases.append(case)
            evidence.append(run(case, actual))

    result = evaluate(cases, evidence)
    assert result.status is Status.SUCCESS
    assert result.outcome == "evaluation_recorded"
    assert result.diagnostics.basis == BASIS
    assert result.diagnostics.input_refs == (REF,)
    assert result.data.live_pass_count == 8
    assert result.data.counts.failed == 2
    assert all(row.verdict is Verdict.PASS and row.required_passes == 4 for row in result.data.metrics)
    assert result.data.results[0].attempt == 1
    assert result.data.results[0].actual["parent_acted"] is False


def test_25_2_missing_measurement_has_distinct_status_and_does_not_promote():
    case = ScenarioCase("A1", "V-01", {}, "A")
    actual = dict(REHEARSAL_CHECKS["A"])
    del actual["scroll_after_gate"]
    result = evaluate([case], [run(case, actual)])
    assert result.status is Status.PARTIAL
    assert result.outcome == "evidence_incomplete"
    assert result.data.unverified_ids == ("A1",)
    assert result.data.results[0].reasons == ("MISSING_SCROLL_AFTER_GATE",)
    assert result.data.live_pass_count == 0


def test_25_3_missing_inputs_and_evidence_with_unapproved_fields_are_errors():
    case = ScenarioCase("A1", "V-01", {}, "A")
    assert evaluate([case], []).data is None
    raw_payload = dict(REHEARSAL_CHECKS["A"]) | {"screen_rect": [1, 2, 3, 4], "mail_body": "private data"}
    result = evaluate([case], [run(case, raw_payload)])
    assert result.status is Status.ERROR
    assert result.outcome is None
    assert result.data is None
    assert result.diagnostics.terminal_error.code == "INVALID_INPUT"
    assert "private data" not in repr(to_jsonable(result))


def test_25_4_live_model_with_mock_screen_retains_observed_behavior_only():
    case = ScenarioCase("A1", "V-01", {}, "A")
    observation = run(case, dict(REHEARSAL_CHECKS["A"]), screen_source=Source.FIXTURE)
    result = evaluate([case], [observation])
    record = result.data.results[0]
    assert result.status is Status.PARTIAL
    assert result.diagnostics.source is Source.FIXTURE
    assert result.diagnostics.basis == BASIS
    assert result.diagnostics.run_id
    assert record.source is Source.FIXTURE
    assert record.model_source is Source.LIVE
    assert record.behavior_verdict is Verdict.PASS
    assert record.verdict is Verdict.UNVERIFIED
    assert result.data.live_pass_count == 0


def test_fixed_twenty_interpretation_samples_use_exact_threshold_without_retry_inflation():
    cases, evidence = [], []
    for index in range(1, 21):
        expected = {"outcome": "instruction_ready", "action": "TAP", "target_key": "settings.display"}
        if index == 20:
            expected["supersedes_previous"] = True
        case = ScenarioCase(f"I{index:02}", "V-02", expected)
        actual = dict(expected)
        if index < 4:
            actual["target_key"] = "settings.screen_timeout"
        cases.append(case)
        evidence.append(run(case, actual))

    result = evaluate(cases, evidence)
    report, = result.data.metrics
    assert report.verdict is Verdict.PASS
    assert (report.eligible_pass_count, report.eligible_count, report.required_passes) == (17, 20, 17)
    evidence[3] = replace(evidence[3], actual=evidence[0].actual)
    result = evaluate(cases, evidence)
    assert result.status is Status.SUCCESS
    assert result.data.metrics[0].verdict is Verdict.FAIL


def test_verified_instruction_can_pass_when_independent_goal_inference_failed():
    expected = {"outcome": "instruction_ready", "action": "TAP", "target_key": "settings.display"}
    case = ScenarioCase("I01", "V-02", expected, source=Source.FIXTURE)
    result = evaluate([case], [run(case, expected | {"outcome": "instruction_only"})])
    assert result.data.results[0].verdict is Verdict.PASS


def test_location_threshold_counts_timeout_and_checks_unsafe_overlay_separately():
    cases, observed = [], []
    for index in range(1, 23):
        kind = "visible" if index <= 20 else "unknown" if index == 21 else "protected"
        expected = {
            "location_kind": kind, "target_id": "settings.display" if index <= 20 else None,
            "direction": None, "container_id": None,
        }
        case = ScenarioCase(f"P{index:02}", "V-03", expected)
        actual = dict(expected) | {
            "bounds_match": index <= 20, "overlay_count": 0, "ground_truth_verified": True,
        }
        if index <= 3:
            actual["request_status"] = "TIMEOUT"
        cases.append(case)
        observed.append(run(case, actual))
    result = evaluate(cases, observed)
    assert result.data.metrics[0].verdict is Verdict.PASS
    assert result.data.metrics[0].eligible_count == 20
    assert result.data.metrics[0].eligible_pass_count == 17
    observed[-1] = replace(observed[-1], actual=observed[-1].actual | {"overlay_count": 1})
    result = evaluate(cases, observed)
    assert result.data.metrics[0].verdict is Verdict.FAIL
    assert result.data.metrics[0].eligible_pass_count == 17


def test_local_timing_boundaries_keep_clock_origins_separate():
    cases, observed = [], []
    for mode, origin in (
        (Mode.CALL_CHILD_LED, "instruction_received"),
        (Mode.POST_CALL_AGENT, "post_call_stable_screen"),
    ):
        for elapsed, action in ((2999, "TAP"), (3000, "SCROLL_DOWN"), (3001, "BACK")):
            case = ScenarioCase(
                f"T-{mode}-{elapsed}", "V-04", {"observation": "none", "elapsed_ms": elapsed, "action": action},
                mode=mode, source=Source.FIXTURE,
            )
            shown = elapsed >= 3000
            actual = dict(
                observation="none", action=action if shown else None, origin=origin, clock_id="parent-monotonic",
                t0_ms=100, checked_ms=100 + elapsed, first_observed_ms=None,
                render_ms=100 + elapsed if shown else None, guide_count=int(shown), safe_to_show=True,
            )
            cases.append(case)
            observed.append(run(case, actual))
    result = evaluate(cases, observed)
    assert result.status is Status.SUCCESS
    assert result.diagnostics.source is Source.FIXTURE
    assert result.data.live_pass_count == 0
    first_shown = observed[1]
    observed[1] = replace(first_shown, actual=first_shown.actual | {"render_ms": 3099})
    wrong_origin = observed[-1]
    observed[-1] = replace(wrong_origin, actual=wrong_origin.actual | {"origin": "instruction_received"})
    result = evaluate(cases, observed)
    assert result.data.results[1].reasons == ("GUIDE_BEFORE_GATE",)
    assert result.data.results[-1].reasons == ("WRONG_TIME_ORIGIN",)


def test_fast_correct_action_at_exact_boundary_must_remain_uninterrupted():
    case = ScenarioCase(
        "T-fast-correct", "V-04", {"observation": "correct", "action": None}, mode=Mode.CALL_CHILD_LED,
    )
    actual = dict(
        observation="correct", action="TAP", origin="instruction_received", clock_id="parent-monotonic",
        t0_ms=100, checked_ms=3100, first_observed_ms=3100, render_ms=3100, guide_count=1, safe_to_show=True,
    )
    result = evaluate([case], [run(case, actual)])
    assert result.data.results[0].reasons == ("GUIDE_ON_FAST_CORRECT_ACTION",)
    assert result.data.metrics[0].verdict is Verdict.FAIL


def test_goal_slots_are_matched_independently_and_reject_plain_private_values():
    cases, evidence = [], []
    for index in range(1, 21):
        expected = {
            "goal_id": None if index <= 2 else "CLOCK_ALARM",
            "approved_slots": {} if index <= 2 else {"time": "06:00", "day": "TOMORROW"},
        }
        case = ScenarioCase(f"G{index:02}", "V-05", expected)
        cases.append(case)
        evidence.append(run(case, expected | {"question_type": "GOAL"}))
    evidence[-1] = replace(evidence[-1], actual=evidence[-1].actual | {"approved_slots": {"time": "07:00"}})
    result = evaluate(cases, evidence)
    assert result.data.metrics[0].eligible_pass_count == 19
    assert result.data.metrics[0].verdict is None
    assert result.data.results[-1].verdict is Verdict.FAIL
    evidence[0] = replace(evidence[0], input_source=None)
    unverified = evaluate(cases, evidence).data.results[0]
    assert unverified.verdict is Verdict.UNVERIFIED
    assert "MISSING_LIVE_INPUT_SOURCE" in unverified.reasons
    evidence[-1] = replace(
        evidence[-1], actual=evidence[-1].actual | {"approved_slots": {"contact": "01012345678"}},
    )
    assert evaluate(cases, evidence).status is Status.ERROR


def test_transition_privacy_and_regression_record_real_failures_and_fixture_checks():
    cases, evidence = [], []
    for metric, checks_by_case in (
        ("V-06", TRANSITION_CHECKS), ("V-07", PRIVACY_CHECKS), ("REGRESSION", REGRESSION_CHECKS),
    ):
        cohort = Source.FIXTURE if metric == "REGRESSION" else Source.LIVE
        for case_id, checks in checks_by_case.items():
            case = ScenarioCase(case_id, metric, {}, source=cohort)
            actual = dict(checks)
            if case_id == "E04":
                actual["error_code"] = "STALE_BASIS"
            if case_id == "V06-REVOKE":
                actual["visible_guides"] = 1
            if case_id == "V07-OVERLAY":
                actual["underlying_tap_applied"] = False
            cases.append(case)
            evidence.append(run(case, actual))
    result = evaluate(cases, evidence)
    assert result.status is Status.SUCCESS
    assert result.diagnostics.source is Source.FIXTURE
    assert [row.verdict for row in result.data.metrics] == [Verdict.FAIL, Verdict.FAIL, Verdict.PASS]
    assert result.data.live_pass_count == len(TRANSITION_CHECKS) + len(PRIVACY_CHECKS) - 2
    scroll = next(row for row in result.data.results if row.case_id == "E09")
    assert scroll.actual["screen_revision_changed"] is False
    assert scroll.actual["layout_revision_changed"] is True


def test_n26_model_runtime_and_budget_gate_is_separate_from_n25_live_count():
    cases, evidence = [], []
    for question in ("GOAL", "TARGET_LOCATION", "ACTION_MATCH", "NEXT_ACTION", "ARRIVAL"):
        case = ScenarioCase(f"M-{question}", "V-08", {"question_type": question})
        actual = dict(question_type=question, gpu="L40S", probability=0.93, latency_ms=570, choice_verified=True)
        cases.append(case)
        evidence.append(run(case, actual))
    budget = ScenarioCase("M-BUDGET", "V-08", {"credits_cents_max": 10_000})
    cases.append(budget)
    evidence.append(run(budget, {"credits_cents": 10_000, "experiments_paused_at_70": True}))

    result = evaluate(cases, evidence)
    assert result.status is Status.SUCCESS
    assert result.data.runtime_metrics[0].verdict is Verdict.PASS
    assert result.data.live_pass_count == 0
    assert not result.data.results
    evidence[-1] = replace(evidence[-1], actual=evidence[-1].actual | {"credits_cents": 10_001})
    assert evaluate(cases, evidence).data.runtime_metrics[0].verdict is Verdict.FAIL
    evidence[-2] = replace(evidence[-2], model_source=Source.FIXTURE)
    assert evaluate(cases, evidence).data.runtime_metrics[0].verdict is Verdict.UNVERIFIED
