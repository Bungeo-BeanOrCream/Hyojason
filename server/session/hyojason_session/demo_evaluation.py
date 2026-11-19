"""N-25. Evaluate sanitized rehearsal evidence against the declared criteria."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from .common.enums import Action, Mode, Source
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, ResultRef, StepStatus, new_id


REHEARSAL_CHECKS = {
    "A": {
        "two_avds": True, "rtc_connected": True, "correct_inside_window": True,
        "guides_on_correct": 0, "back_after_gate": True, "scroll_after_gate": True,
        "tap_after_gate": True, "parent_acted": True, "child_progress_hidden": True,
    },
    "B": {
        "goal_id": "CLOCK_ALARM", "opening_instruction_accepted": True,
        "two_avds": True, "rtc_ended": True, "stt_ended": True, "help_id_retained": True,
        "stale_guides": 0, "post_call_stable_screen": True, "next_action_after_gate": True,
        "parent_acted": True, "alarm_matches": True, "arrival_count": 1,
        "completion_count": 1, "trail_cleared": True, "child_progress_hidden": True,
    },
}
TRANSITION_CHECKS = {
    "V06-HANGUP": {"mode": "POST_CALL_AGENT", "rtc_ended": True, "stt_ended": True,
                   "help_id_retained": True, "stale_guides": 0},
    "V06-NO-GOAL": {"post_call_started": False, "arrival_count": 0},
    "V06-RECONNECT": {"mode": "CALL_CHILD_LED", "post_call_started": False, "arrival_count": 0},
    "V06-NOT-ARRIVED": {"arrival_count": 0},
    "V06-COMPLETE": {"completion_count": 1, "stale_completions_applied": 0, "trail_cleared": True},
    "V06-REVOKE": {"visible_guides": 0, "capture_stopped": True},
    "V06-CHILD-VIEW": {"child_progress_hidden": True},
}
PRIVACY_CHECKS = {
    "V07-CONNECT": {"screen_video_tracks": 0, "persisted_raw_items": 0, "persisted_personal_items": 0},
    "V07-RECONNECT": {"suggestions_replayed": 0, "child_progress_hidden": True},
    "V07-RESTART": {"old_help_items_reused": 0, "new_help_id": True},
    "V07-RECONSENT": {"restarted_before_consent": 0, "old_help_items_reused": 0},
    "V07-OVERLAY": {"underlying_tap_applied": True, "overlay_intercepted_tap": False},
}
REGRESSION_CHECKS = {
    "E01": {"status": "success", "outcome": "no_candidates", "candidate_count": 0,
            "downstream_model_calls": 0, "arrival_count": 0},
    "E02": {"status": "partial", "outcome": "search_incomplete", "coverage": "partial",
            "candidate_count": 0, "reobservation_requested": True},
    "E03": {"status": "partial", "outcome": "search_incomplete", "coverage": "partial",
            "recovery_candidates": 0, "only_direct_verified": True},
    "E04": {"status": "error", "outcome": None, "visible_guides": 0},
    "E05": {"visible_guides": 0, "waiting_then_followed": True},
    "E06": {"old_instruction_rejected": True, "old_timer_guides": 0, "new_timer_starts": 1},
    "E07": {"downstream_model_calls": 0, "new_timer_starts": 0, "visible_guides": 0},
    "E08": {"back_guides": 0},
    "E09": {"screen_revision_changed": False, "layout_revision_changed": True,
            "instruction_revision_changed": False, "t0_changed": False, "next_action_calls": 0},
    "E10": {"mode": "POST_CALL_AGENT", "old_timer_guides": 0, "old_instruction_rejected": True},
    "E11": {"visible_guides": 0, "protection_message_valid": True},
    "E12": {"audio_route_evaluations": 1, "audio_route_changed": False},
    "E13": {"arrival_count": 0, "automated_actions": 0},
    "E14": {"old_epoch_rejected": True, "coordinates_rematched": True,
            "stale_guides": 0, "post_call_started": False},
}

_FIXED = {"V-06": TRANSITION_CHECKS, "V-07": PRIVACY_CHECKS, "REGRESSION": REGRESSION_CHECKS}
_INSTRUCTIONS = frozenset(f"I{i:02}" for i in range(1, 21))
_QUESTIONS = frozenset({"GOAL", "TARGET_LOCATION", "ACTION_MATCH", "NEXT_ACTION", "ARRIVAL"})
_GOALS = frozenset({"SET_FONT", "SET_TIMEOUT", "CLOCK_ALARM", "MAP_ROUTE", "SMS_SEND", "MAIL_OPEN"})
_LOCATION_KINDS = frozenset({"visible", "below_fold", "unknown", "protected"})
_OBSERVATIONS = frozenset({"correct", "none", "wrong", "pending"})
_INSTRUCTION_OUTCOMES = frozenset({
    "instruction_ready", "instruction_only", "waiting", "goal_only", "target_unknown", "ambiguous_instruction",
    "policy_denied", "non_instruction", "no_instruction", "uninterpretable", "timeout",
})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:#/+~-]{0,127}\Z")
_SLOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_ASSET_REF = re.compile(r"(?:fixture|demo)-[a-z][a-z0-9_-]{0,63}\Z")
_PHONE_LIKE = re.compile(r"(?:\+?[0-9][-.]?){7,}\Z")
_SLOT_VALUES = frozenset({"AM", "PM", "TODAY", "TOMORROW", "today", "tomorrow", "am", "pm"})


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"


Fields = Mapping[str, object]


@dataclass(frozen=True)
class ScenarioCase:
    case_id: str
    metric_id: str
    expected: Fields
    scenario_id: str | None = None
    mode: Mode | str | None = None
    source: Source | str = Source.LIVE


@dataclass(frozen=True)
class RunEvidence:
    case_id: str
    attempt: int
    trace_id: str
    source: Source | str
    actual: Fields
    basis: Basis = field(default_factory=Basis)
    screen_source: Source | str | None = None
    model_source: Source | str | None = None
    input_source: Source | str | None = None
    device_revision: str | None = None
    app_revision: str | None = None
    profile_revision: str | None = None
    model_revision: str | None = None
    failure_domain: str | None = None


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    metric_id: str
    scenario_id: str | None
    mode: Mode | str | None
    expected: Fields
    actual: Fields | None
    verdict: Verdict
    behavior_verdict: Verdict
    reasons: tuple[str, ...]
    failure_domain: str | None
    attempt: int | None
    trace_id: str | None
    source: Source | None
    screen_source: Source | str | None
    model_source: Source | str | None
    input_source: Source | str | None
    device_revision: str | None
    app_revision: str | None
    profile_revision: str | None
    model_revision: str | None
    basis: Basis | None


@dataclass(frozen=True)
class StatusCounts:
    passed: int
    failed: int
    unverified: int


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    source: Source
    scenario_id: str | None
    verdict: Verdict | None
    counts: StatusCounts
    eligible_count: int
    eligible_pass_count: int
    expected_minimum: int
    required_passes: int | None
    missing_requirements: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationData:
    results: tuple[CaseResult, ...]
    counts: StatusCounts
    live_pass_count: int
    unverified_ids: tuple[str, ...]
    criteria_revision: int
    metrics: tuple[MetricResult, ...]
    runtime_results: tuple[CaseResult, ...]
    runtime_metrics: tuple[MetricResult, ...]


def _uint(value: object) -> bool:
    return type(value) is int and value >= 0


def _token(value: object) -> bool:
    return (
        isinstance(value, str) and _ID.fullmatch(value) is not None
        and _PHONE_LIKE.fullmatch(value) is None and "://" not in value
    )


def _source(value):
    return None if value is None else Source(value)


def _safe_slot(value: object) -> bool:
    # IDs refer to staged demo assets. Never accept free-form text, account IDs,
    # phone numbers, or local media as values in the evaluation manifest.
    return (
        isinstance(value, bool) or (_uint(value) and value <= 1440)
        or isinstance(value, str) and (
            value in _SLOT_VALUES or _ASSET_REF.fullmatch(value) is not None
            or re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", value) is not None
            or value.isdecimal() and len(value) <= 4 and value.isascii() and int(value) <= 1440
        )
    )


def _slots(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and _SLOT_ID.fullmatch(key) and _safe_slot(item)
        for key, item in value.items()
    )


def _safe_field(name, value, example):
    if name == "approved_slots":
        return _slots(value)
    if value is None:
        return example is None or name in _NULLABLE
    if name == "probability":
        return type(value) in (int, float) and 0 <= value <= 1 and isfinite(value)
    if name == "bounds_match":
        return type(value) is bool
    if name in _NULLABLE_UINTS or type(example) is int:
        return _uint(value)
    if type(example) is bool:
        return type(value) is bool
    return _token(value)


_NULLABLE_UINTS = {"first_observed_ms", "render_ms", "speech_end_to_parent_ms"}
_NULLABLE = _NULLABLE_UINTS | {"action", "target_key", "target_id", "container_id", "direction", "goal_id", "outcome",
                             "probability", "latency_ms", "choice_verified"}
_LOCATION_FIELDS = {"location_kind": "", "target_id": None, "container_id": None, "direction": None}
_TIMING_FIELDS = {
    "observation": "", "action": None, "origin": "", "clock_id": "", "t0_ms": 0, "checked_ms": 0,
    "first_observed_ms": None, "render_ms": None, "guide_count": 0, "safe_to_show": True,
}
_GOAL_FIELDS = {"goal_id": None, "approved_slots": {}}
_INTERPRET_FIELDS = {"outcome": "", "action": None, "target_key": None}
_MODEL_FIELDS = {"question_type": "", "gpu": "", "probability": 0.0, "latency_ms": 0,
                 "choice_verified": True}
_BUDGET_FIELDS = {"credits_cents": 0, "experiments_paused_at_70": True}


def _fields(value, schema, optional=()):
    if not isinstance(value, Mapping):
        raise ValueError("invalid evidence")
    extras = dict(optional)
    allowed = schema | extras
    if any(key not in allowed or not _safe_field(key, item, allowed[key]) for key, item in value.items()):
        raise ValueError("invalid evidence field")
    return {key: dict(item) if key == "approved_slots" else item for key, item in value.items()}


def _expected(case: ScenarioCase) -> tuple[dict, dict, dict]:
    """Return validated expectations, observed field schema, and optional observed fields."""
    expected = case.expected
    if not isinstance(expected, Mapping):
        raise ValueError("invalid expectation")
    metric = case.metric_id
    if metric == "V-01" or metric in _FIXED:
        if metric == "REGRESSION" and Source(case.source) is not Source.FIXTURE:
            raise ValueError("regressions require fixture provenance")
        checks = REHEARSAL_CHECKS[case.scenario_id] if metric == "V-01" else _FIXED[metric][case.case_id]
        if any(key not in checks or type(item) is not type(checks[key]) or item != checks[key]
               for key, item in expected.items()):
            raise ValueError("fixed expectation mismatch")
        extra = {"error_code": ""} if case.case_id == "E04" else {}
        return dict(checks), dict(checks), extra
    if metric == "V-02":
        shape = _INTERPRET_FIELDS | ({"supersedes_previous": True} if case.case_id == "I20" else {})
        result = _fields(expected, shape)
        if (case.case_id not in _INSTRUCTIONS or set(result) != set(shape)
                or result["outcome"] not in _INSTRUCTION_OUTCOMES
                or result["action"] is not None and result["action"] not in set(Action)
                or case.case_id == "I20" and result["supersedes_previous"] is not True):
            raise ValueError("invalid interpretation expectation")
        return result, shape, {}
    if metric == "V-03":
        result = _fields(expected, _LOCATION_FIELDS)
        if set(result) != set(_LOCATION_FIELDS) or result["location_kind"] not in _LOCATION_KINDS:
            raise ValueError("invalid location expectation")
        kind = result["location_kind"]
        if ((kind in ("visible", "below_fold")) != (result["target_id"] is not None)
                or (kind == "below_fold") != (result["container_id"] is not None)
                or result["direction"] not in (("down", "up") if kind == "below_fold" else (None,))):
            raise ValueError("invalid eligible location")
        return result, _LOCATION_FIELDS | {
            "ground_truth_verified": True, "bounds_match": None, "overlay_count": 0,
        }, {"request_status": ""}
    if metric == "V-04":
        result = _fields(expected, {"observation": "", "action": None}, {"elapsed_ms": 0})
        if (result.get("observation") not in _OBSERVATIONS or "action" not in result
                or result["action"] not in (None, Action.TAP, Action.BACK, Action.SCROLL_DOWN)
                or case.mode is None):
            raise ValueError("invalid timing expectation")
        return result, _TIMING_FIELDS, {
            "speech_end_to_parent_ms": None, "speech_clock_id": "", "decision_delayed": True,
        }
    if metric == "V-05":
        result = _fields(expected, _GOAL_FIELDS)
        if (set(result) != set(_GOAL_FIELDS) or result["goal_id"] not in _GOALS | {None}
                or result["goal_id"] is None and result["approved_slots"]):
            raise ValueError("invalid goal expectation")
        return result, _GOAL_FIELDS | {"question_type": ""}, {}
    if metric == "V-08":
        if expected == {"credits_cents_max": 10_000}:
            return {"credits_cents_max": 10_000}, _BUDGET_FIELDS, {}
        if set(expected) == {"question_type"} and expected["question_type"] in _QUESTIONS:
            return dict(expected), _MODEL_FIELDS, {"request_status": ""}
    raise ValueError("unknown or invalid expectation")


def _basis_valid(basis):
    if not isinstance(basis, Basis):
        return False
    return all(value is None or (_token(value) if key.endswith("_id") or key == "projection_epoch" else _uint(value))
               for key, value in vars(basis).items())


def _evidence(run: RunEvidence, shape: dict, extra: dict) -> dict:
    if (not _uint(run.attempt) or run.attempt < 1 or not _token(run.trace_id)
            or not _basis_valid(run.basis) or run.failure_domain not in (None, "FUNCTION", "DEVICE", "CONTRACT")
            or any(value is not None and not _token(value) for value in (
                run.device_revision, run.app_revision, run.profile_revision, run.model_revision,
            ))):
        raise ValueError("invalid run metadata")
    Source(run.source)
    for component in (run.screen_source, run.model_source, run.input_source):
        _source(component)
    result = _fields(run.actual, shape, extra)
    for key, options in {
        "mode": set(Mode), "origin": {"instruction_received", "post_call_stable_screen"},
        "observation": _OBSERVATIONS, "direction": {"down", "up", None},
        "request_status": {"SUCCESS", "TIMEOUT", "ERROR", "PATH_MISSING"},
        "status": {"success", "partial", "error"},
        "question_type": _QUESTIONS, "location_kind": _LOCATION_KINDS,
        "action": set(Action) | {None}, "goal_id": _GOALS | {None},
        "error_code": {ErrorCode.STALE_BASIS, ErrorCode.DEPENDENCY_UNAVAILABLE,
                       ErrorCode.INVALID_INPUT, ErrorCode.TIMEOUT},
    }.items():
        if key in result and result[key] not in options:
            raise ValueError("invalid evidence value")
    if result.get("speech_end_to_parent_ms") is not None and "speech_clock_id" not in result:
        raise ValueError("missing speech clock")
    return result


def _compare(expected, actual):
    mismatches = tuple(f"MISMATCH_{key.upper()}" for key, value in expected.items()
                       if key in actual and actual[key] != value)
    if mismatches:
        return Verdict.FAIL, mismatches
    missing = tuple(f"MISSING_{key.upper()}" for key in expected if key not in actual)
    if missing:
        return Verdict.UNVERIFIED, missing
    return Verdict.PASS, ()


def _location(expected, actual):
    if actual.get("ground_truth_verified") is not True:
        return Verdict.UNVERIFIED, ("MISSING_LOCAL_GROUND_TRUTH",)
    kind = expected["location_kind"]
    if kind in ("unknown", "protected"):
        return _compare({"location_kind": kind, "overlay_count": 0}, actual)
    if actual.get("request_status", "SUCCESS") != "SUCCESS":
        return Verdict.FAIL, ("ELIGIBLE_LOCATION_NOT_RESOLVED",)
    verdict, reasons = _compare(expected, actual)
    if verdict is not Verdict.PASS or kind != "visible":
        return verdict, reasons
    if actual.get("bounds_match") is None:
        return Verdict.UNVERIFIED, ("MISSING_BOUNDS_MATCH",)
    return _compare({"bounds_match": True}, actual)


def _timing(case, expected, actual):
    absent = tuple(f"MISSING_{key.upper()}" for key in _TIMING_FIELDS if key not in actual)
    if absent:
        return Verdict.UNVERIFIED, absent
    origin = "instruction_received" if case.mode == Mode.CALL_CHILD_LED else "post_call_stable_screen"
    if actual["origin"] != origin:
        return Verdict.FAIL, ("WRONG_TIME_ORIGIN",)
    observation, t0, checked = (actual[key] for key in ("observation", "t0_ms", "checked_ms"))
    observed, rendered = actual["first_observed_ms"], actual["render_ms"]
    count, action = actual["guide_count"], actual["action"]
    if (checked < t0 or any(value is not None and not t0 <= value <= checked for value in (observed, rendered))
            or (observation == "none") != (observed is None)
            or (count > 0) != (rendered is not None) or (count > 0) != (action is not None)):
        return Verdict.UNVERIFIED, ("INCONSISTENT_TIME_EVIDENCE",)
    if observation != expected["observation"]:
        return Verdict.FAIL, ("WRONG_OBSERVATION_BRANCH",)
    elapsed = checked - t0
    if "elapsed_ms" in expected and elapsed != expected["elapsed_ms"]:
        return Verdict.FAIL, ("WRONG_TEST_BOUNDARY",)
    if rendered is not None and rendered - t0 < 3000:
        return Verdict.FAIL, ("GUIDE_BEFORE_GATE",)
    if count and (not actual["safe_to_show"] or observation == "pending"):
        return Verdict.FAIL, ("GUIDE_WHILE_BLOCKED",)
    if observation == "correct":
        if observed - t0 > 3000:
            return Verdict.FAIL, ("CORRECT_ACTION_OUTSIDE_WINDOW",)
        if count:
            return Verdict.FAIL, ("GUIDE_ON_FAST_CORRECT_ACTION",)
    expected_action = expected["action"]
    if count and action != expected_action:
        return Verdict.FAIL, ("WRONG_GUIDE_ACTION",)
    if count > 1:
        return Verdict.FAIL, ("DUPLICATE_GUIDE",)
    if (expected_action is not None and elapsed >= 3000 and observation in ("none", "wrong")
            and actual["safe_to_show"] and not count):
        return Verdict.FAIL, ("MISSING_READY_GUIDE",)
    return Verdict.PASS, ()


def _budget(actual):
    if "credits_cents" not in actual:
        return Verdict.UNVERIFIED, ("MISSING_RECORDED_CREDITS",)
    if actual["credits_cents"] > 10_000:
        return Verdict.FAIL, ("CREDIT_CAP_EXCEEDED",)
    if actual["credits_cents"] >= 7_000:
        return _compare({"experiments_paused_at_70": True}, actual)
    return Verdict.PASS, ()


def _behavior(case, expected, actual):
    metric = case.metric_id
    if (metric == "V-02" and expected["outcome"] == "instruction_ready"
            and actual.get("outcome") == "instruction_only"):
        # N-15 can verify a button directive while separate goal inference fails.
        return _compare(expected | {"outcome": "instruction_only"}, actual)
    if metric == "V-03":
        return _location(expected, actual)
    if metric == "V-04":
        return _timing(case, expected, actual)
    if metric == "V-05":
        return _compare(expected | {"question_type": "GOAL"}, actual)
    if metric == "V-08":
        if "credits_cents_max" in expected:
            return _budget(actual)
        if actual.get("request_status", "SUCCESS") != "SUCCESS":
            return Verdict.FAIL, ("MODEL_REQUEST_FAILED",)
        checks = expected | {"gpu": "L40S", "choice_verified": True}
        verdict, reasons = _compare(checks, actual)
        if verdict is Verdict.PASS and any(actual.get(key) is None for key in ("probability", "latency_ms")):
            return Verdict.UNVERIFIED, ("MISSING_MODEL_TELEMETRY",)
        return verdict, reasons
    if case.case_id == "E04" and "error_code" not in actual:
        return Verdict.UNVERIFIED, ("MISSING_ERROR_CODE",)
    if case.case_id == "E04" and actual["error_code"] not in ("STALE_BASIS", "DEPENDENCY_UNAVAILABLE"):
        return Verdict.FAIL, ("WRONG_ERROR_CODE",)
    return _compare(expected, actual)


def _missing_live(case, run, expected):
    metric = case.metric_id
    required = {}
    if metric in ("V-01", "V-03", "V-04", "V-06", "V-07"):
        required |= {"screen_source": Source.LIVE}
    if metric in ("V-01", "V-02", "V-05"):
        required |= {"input_source": Source.LIVE}
    model_used = (metric in ("V-01", "V-05")
                  or metric == "V-03" and expected["location_kind"] in ("visible", "below_fold")
                  or metric == "V-08" and "question_type" in expected)
    if model_used:
        required |= {"model_source": Source.LIVE}
    missing = [name.upper() for name, value in required.items() if getattr(run, name) != value]
    if metric in ("V-01", "V-02", "V-03", "V-04", "V-06", "V-07"):
        missing.extend(name.upper() for name in ("device_revision", "app_revision", "profile_revision")
                       if not getattr(run, name))
    if model_used and run.model_revision is None:
        missing.append("MODEL_REVISION")
    return tuple(f"MISSING_LIVE_{name}" for name in missing)


def _result(case, expected, record, source):
    intended = Source(case.source)
    common = (case.case_id, case.metric_id, case.scenario_id, case.mode, expected)
    if record is None:
        return CaseResult(
            *common, actual=None, verdict=Verdict.UNVERIFIED, behavior_verdict=Verdict.UNVERIFIED,
            reasons=("MISSING_FIRST_ATTEMPT",), failure_domain="DEVICE", attempt=None, trace_id=None, source=None,
            screen_source=None, model_source=None, input_source=None, device_revision=None, app_revision=None,
            profile_revision=None, model_revision=None, basis=None,
        )
    run, actual = record
    components = (source, intended, run.source, run.screen_source, run.model_source, run.input_source)
    origin = Source.FIXTURE if Source.FIXTURE in components else Source.LIVE
    verdict, reasons = _behavior(case, expected, actual)
    behavior = verdict
    if intended is Source.LIVE and origin is Source.FIXTURE:
        verdict = Verdict.UNVERIFIED
        reasons += ("MOCK_COMPONENT_IN_LIVE_CASE",)
    if origin is Source.LIVE:
        missing = _missing_live(case, run, expected)
        if missing:
            verdict = Verdict.UNVERIFIED
            reasons += missing
    return CaseResult(
        *common, actual=actual, verdict=verdict, behavior_verdict=behavior, reasons=reasons,
        failure_domain=(run.failure_domain or ("FUNCTION" if verdict is Verdict.FAIL else "DEVICE")
                        if verdict is not Verdict.PASS else None),
        attempt=run.attempt, trace_id=run.trace_id, source=origin, screen_source=run.screen_source,
        model_source=run.model_source, input_source=run.input_source, device_revision=run.device_revision,
        app_revision=run.app_revision, profile_revision=run.profile_revision, model_revision=run.model_revision,
        basis=run.basis,
    )


def _counts(rows):
    counts = Counter(row.verdict for row in rows)
    return StatusCounts(counts[Verdict.PASS], counts[Verdict.FAIL], counts[Verdict.UNVERIFIED])


def _metric(metric, group, source, rows):
    # Sample counts and quality thresholds are independent of recording status.
    required = ()
    minimum, required_passes = 1, None
    eligible = rows
    ids = {row.case_id for row in rows}
    missing = []
    if metric == "V-01":
        minimum, required_passes = 5, 4
        if len(rows) > 5:
            missing.append("REQUIRES_FIRST_FIVE_FIXED_SAMPLES")
    elif metric == "V-02":
        minimum, required_passes = 20, 17
        required = sorted(_INSTRUCTIONS)
    elif metric == "V-03":
        minimum = 20
        eligible = [row for row in rows if row.expected["location_kind"] in ("visible", "below_fold")]
        required_passes = (85 * len(eligible) + 99) // 100
        if not eligible:
            missing.append("NO_ELIGIBLE_LOCATION_SAMPLES")
        if not any(row.expected["location_kind"] in ("unknown", "protected") for row in rows):
            missing.append("NO_UNSAFE_LOCATION_CONTROLS")
    elif metric == "V-04":
        if source is Source.FIXTURE:
            boundary = {(row.mode, row.expected.get("elapsed_ms")) for row in rows}
            for mode in Mode:
                for elapsed in (2999, 3000, 3001):
                    if (mode, elapsed) not in boundary:
                        missing.append(f"{mode}_{elapsed}")
        elif not any(row.expected["observation"] == "correct" for row in rows):
            missing.append("FAST_CORRECT_SAMPLE_REQUIRED")
    elif metric == "V-05":
        minimum = 20
        if not any(row.expected["goal_id"] is None for row in rows):
            missing.append("NO_WITHHELD_GOAL_SAMPLES")
    elif metric in _FIXED:
        required = tuple(_FIXED[metric])
        minimum = len(required)
    elif metric == "V-08":
        minimum = 6
        questions = {row.expected.get("question_type") for row in rows}
        missing.extend(sorted(_QUESTIONS - questions))
        if not any("credits_cents_max" in row.expected for row in rows):
            missing.append("MISSING_CREDIT_LEDGER")
        if len({row.model_revision for row in rows if "question_type" in row.expected
                and row.model_revision is not None}) > 1:
            missing.append("MIXED_MODEL_REVISIONS")
    missing.extend(f"MISSING_{case_id}" for case_id in required if case_id not in ids)
    if len(rows) < minimum:
        missing.append("INSUFFICIENT_SAMPLES")
    counts = _counts(rows)
    passing = sum(row.verdict is Verdict.PASS for row in eligible)
    if missing or counts.unverified:
        verdict = Verdict.UNVERIFIED
    elif metric == "V-03":
        unsafe = any(row.verdict is Verdict.FAIL for row in rows if row not in eligible)
        verdict = Verdict.PASS if passing >= required_passes and not unsafe else Verdict.FAIL
    elif required_passes is not None:
        verdict = Verdict.PASS if passing >= required_passes else Verdict.FAIL
    elif metric == "V-05":
        # Report accuracy and the individual failed goal/slot matches. The
        # experiment plan does not define a pass threshold for this metric.
        verdict = None
    else:
        verdict = Verdict.FAIL if counts.failed else Verdict.PASS
    return MetricResult(metric, source, group, verdict, counts, len(eligible), passing,
                        minimum, required_passes, tuple(missing))


def evaluate_end_to_end(
    scenarios: Sequence[ScenarioCase],
    observed_runs: Sequence[RunEvidence],
    criteria_revision: int,
    expected_modes: Sequence[Mode | str],
    *,
    basis: Basis,
    trace_id: str | None = None,
    source: Source | str = Source.LIVE,
    input_refs: Sequence[ResultRef] = (),
) -> NodeResult[EvaluationData]:
    """Evaluate the first recorded attempt per case without merging provenance cohorts."""
    diag_basis = basis if _basis_valid(basis) else Basis()
    diag_trace = trace_id if _token(trace_id) else new_id("trace")
    try:
        origin = Source(source)
    except (TypeError, ValueError):
        run = NodeRun("N-25", diag_trace, Source.FIXTURE)
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", diag_basis)

    run = NodeRun("N-25", diag_trace, origin)
    try:
        if any(not isinstance(ref, ResultRef) or not all(_token(value) for value in vars(ref).values())
               for ref in input_refs):
            raise ValueError("invalid input references")
    except (TypeError, ValueError):
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", diag_basis)
    try:
        if not _basis_valid(basis) or not _uint(criteria_revision) or not scenarios or not observed_runs:
            raise ValueError("missing evaluation input")
        if trace_id is not None and not _token(trace_id):
            raise ValueError("invalid evaluation trace")
        modes = frozenset(Mode(mode) for mode in expected_modes)
        if not modes:
            raise ValueError("missing expected modes")
        inputs = {}
        for case in scenarios:
            if (not isinstance(case, ScenarioCase) or not _token(case.case_id) or case.case_id in inputs
                    or case.mode is not None and Mode(case.mode) not in modes
                    or case.scenario_id is not None and not _token(case.scenario_id)):
                raise ValueError("invalid case")
            Source(case.source)
            inputs[case.case_id] = (case, *_expected(case))
        attempts: dict[str, set[int]] = {}
        first = {}
        for evidence in observed_runs:
            if not isinstance(evidence, RunEvidence) or evidence.case_id not in inputs:
                raise ValueError("unmatched case evidence")
            _, _, shape, extra = inputs[evidence.case_id]
            actual = _evidence(evidence, shape, extra)
            seen = attempts.setdefault(evidence.case_id, set())
            if evidence.attempt in seen:
                raise ValueError("duplicate attempt")
            seen.add(evidence.attempt)
            if evidence.attempt == 1:
                first[evidence.case_id] = (evidence, actual)
    except (TypeError, ValueError, KeyError):
        return run.error(ErrorCode.INVALID_INPUT, "validate_input", diag_basis, input_refs=input_refs)
    run.step("validate_input", StepStatus.SUCCESS)

    results = []
    runtime = []
    grouped = {}
    for case_id, (case, expected, _, _) in inputs.items():
        result = _result(case, expected, first.get(case_id), origin)
        destination = runtime if case.metric_id == "V-08" else results
        destination.append(result)
        group = case.scenario_id if case.metric_id == "V-01" else None
        grouped.setdefault((case.metric_id, group, Source(case.source)), []).append(result)
        if result.source is Source.FIXTURE:
            run.source = Source.FIXTURE
    reports = []
    runtime_reports = []
    for (metric, group, cohort), rows in grouped.items():
        report = _metric(metric, group, cohort, rows)
        (runtime_reports if metric == "V-08" else reports).append(report)
    all_results = results + runtime
    unverified = tuple(row.case_id for row in all_results if row.verdict is Verdict.UNVERIFIED)
    data = EvaluationData(
        results=tuple(results), counts=_counts(results),
        live_pass_count=sum(row.source is Source.LIVE and row.verdict is Verdict.PASS for row in results),
        unverified_ids=unverified, criteria_revision=criteria_revision, metrics=tuple(reports),
        runtime_results=tuple(runtime), runtime_metrics=tuple(runtime_reports),
    )
    run.step("classify_provenance", StepStatus.SUCCESS, count=len(all_results))
    if unverified or any(report.verdict is Verdict.UNVERIFIED for report in reports + runtime_reports):
        run.step("evaluate_evidence", StepStatus.SKIPPED, "EVIDENCE_INCOMPLETE", count=len(unverified))
        return run.partial("evidence_incomplete", data, basis, input_refs)
    run.step("evaluate_evidence", StepStatus.SUCCESS, count=len(all_results))
    return run.success("evaluation_recorded", data, basis, input_refs)
