"""Versioned B-08 case plan from the checked-in rehearsal samples."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .common.enums import Mode, Source
from .demo_evaluation import (
    PRIVACY_CHECKS, REGRESSION_CHECKS, REHEARSAL_CHECKS, TRANSITION_CHECKS, ScenarioCase,
)


CRITERIA_REVISION = 1
EXPECTED_MODES = (Mode.CALL_CHILD_LED, Mode.POST_CALL_AGENT)
MODEL_QUESTIONS = ("GOAL", "TARGET_LOCATION", "ACTION_MATCH", "NEXT_ACTION", "ARRIVAL")


def build_demo_suite(
    instructions: Sequence[Mapping],
    positions: Sequence[Mapping],
    goals: Sequence[Mapping],
    *,
    cohort: Source,
) -> tuple[ScenarioCase, ...]:
    """Keep the same case identities and thresholds for both rehearsal cohorts."""
    cases = [
        ScenarioCase(f"{scenario}{trial:02d}", "V-01", dict(REHEARSAL_CHECKS[scenario]),
                     scenario_id=scenario, source=cohort)
        for scenario in ("A", "B")
        for trial in range(1, 6)
    ]
    for sample in instructions:
        target = sample["target_key"]
        expected = {
            "outcome": sample.get("outcome", "instruction_ready"),
            "action": "TAP" if target is not None else None,
            "target_key": target,
        }
        if sample.get("supersedes_previous") is True:
            expected["supersedes_previous"] = True
        cases.append(ScenarioCase(sample["id"], "V-02", expected, source=cohort))
    for sample in positions:
        expected = {
            key: sample.get(key)
            for key in ("location_kind", "target_id", "container_id", "direction")
        }
        cases.append(ScenarioCase(sample["id"], "V-03", expected, source=cohort))
    cases.extend(_timing_cases(cohort))
    for sample in goals:
        expected = {"goal_id": sample["goal_id"], "approved_slots": sample["approved_slots"]}
        cases.append(ScenarioCase(sample["id"], "V-05", expected, source=cohort))
    cases.extend(ScenarioCase(case_id, "V-06", dict(checks), source=cohort)
                 for case_id, checks in TRANSITION_CHECKS.items())
    cases.extend(ScenarioCase(case_id, "V-07", dict(checks), source=cohort)
                 for case_id, checks in PRIVACY_CHECKS.items())
    cases.extend(ScenarioCase(case_id, "REGRESSION", dict(checks), source=Source.FIXTURE)
                 for case_id, checks in REGRESSION_CHECKS.items())
    cases.extend(
        ScenarioCase(f"V08-{question}", "V-08", {"question_type": question}, source=cohort)
        for question in MODEL_QUESTIONS
    )
    cases.append(ScenarioCase("V08-budget", "V-08", {"credits_cents_max": 10_000}, source=cohort))
    return tuple(cases)


def _timing_cases(cohort: Source) -> list[ScenarioCase]:
    cases = []
    for mode, label in zip(EXPECTED_MODES, ("call", "postcall"), strict=True):
        cases.append(ScenarioCase(
            f"T-{label}-correct", "V-04", {"observation": "correct", "action": None},
            mode=mode, source=cohort,
        ))
        actions = ("TAP", "BACK", "SCROLL_DOWN") if mode == Mode.CALL_CHILD_LED else ("TAP",)
        for action in actions:
            observation = "wrong" if action == "BACK" else "none"
            cases.append(ScenarioCase(
                f"T-{label}-{action.lower()}-live", "V-04",
                {"observation": observation, "action": action},
                mode=mode, source=cohort,
            ))
            for elapsed in (2999, 3000, 3001):
                cases.append(ScenarioCase(
                    f"T-{label}-{action.lower()}-{elapsed}", "V-04",
                    {"observation": observation, "action": action, "elapsed_ms": elapsed},
                    mode=mode, source=Source.FIXTURE,
                ))
        cases.append(ScenarioCase(
            f"T-{label}-correct-3000", "V-04",
            {"observation": "correct", "action": None, "elapsed_ms": 3000},
            mode=mode, source=Source.FIXTURE,
        ))
    return cases
