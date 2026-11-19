"""D 세션 스냅샷을 C의 읽기 전용 판단 입력으로 변환한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any, cast

from ..contracts import (
    Action,
    Basis,
    Goal,
    GoalId,
    Instruction,
    InstructionTrace,
    JsonValue,
    Mode,
    NavigationTrail,
    ScreenState,
    TrailEntry,
)
from .b05_workflow import AgentSnapshot
from .profiles import GOAL_LABELS

_ACTIONS = {"TAP", "BACK", "SCROLL_DOWN", "SCROLL_UP", "INPUT_HINT", "WAIT"}


def _field_map(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: getattr(value, item.name) for item in fields(value)}
    raise TypeError("세션 입력은 dataclass 또는 mapping이어야 합니다")


def _screen(value: object, *, historical: bool = False) -> ScreenState:
    item = _field_map(value)
    return ScreenState(
        projection_epoch=item["projection_epoch"],
        screen_revision=item["screen_revision"],
        screen_kind=item["screen_kind"],
        target_ids=tuple(item.get("target_ids", ())),
        evidence_codes=tuple(item.get("evidence_codes", ())),
        observation_health=item.get(
            "observation_health", "ACTIVE" if historical else "GAP"
        ),
    )


def _saved_trail(value: object) -> NavigationTrail:
    item = _field_map(value)
    if isinstance(value, Mapping):
        entries, directives = item["entries"], item["directives"]
    else:
        entries, directives = getattr(value, "entries"), getattr(value, "directives")
    return NavigationTrail(
        entries=tuple(
            TrailEntry(
                from_screen=_screen(edge["from_screen"], historical=True),
                to_screen=_screen(edge["to_screen"], historical=True),
                projection_epoch=edge["projection_epoch"],
                evidence_codes=tuple(edge["evidence_codes"]),
                reversible=edge["reversible"],
                observed_seq=edge["observed_seq"],
            )
            for edge in map(_field_map, entries)
        ),
        directives=tuple(
            InstructionTrace(
                instruction_id=record["instruction_id"],
                instruction_revision=record["instruction_revision"],
                target_id=record["target_id"],
                origin_screen_revision=record["origin_screen_revision"],
                evidence_codes=tuple(record["evidence_codes"]),
                outcome=record["outcome"],
            )
            for record in map(_field_map, directives)
        ),
        gap_after_seq=item.get("gap_after_seq"),
        history_truncated=item.get("history_truncated", False),
    )


def from_session(
    snapshot: object,
    trail: object,
    *,
    processing_allowed: bool,
    basis: object | None = None,
) -> AgentSnapshot:
    """Use the authenticated D reducer's snapshot and trail from the same observation."""
    state = _field_map(snapshot)
    if basis is None:
        basis = getattr(snapshot, "basis", None)
    if basis is None:
        raise ValueError("공유 snapshot에는 실행 Basis를 별도로 전달해야 합니다")
    basis_fields = _field_map(basis)
    for key in ("pair_id", "call_id", "help_id", "mode_revision", "consent_revision"):
        if key in state and state[key] != basis_fields[key]:
            raise ValueError("세션 상태와 실행 Basis의 범위가 다릅니다")
    run_basis = Basis(**{item.name: basis_fields[item.name] for item in fields(Basis)})
    trail_help = _field_map(trail).get("help_id")
    if trail_help is not None and trail_help != state["help_id"]:
        raise ValueError("다른 도움의 방문 이력")
    mode = state["mode"]
    if mode not in {"CALL_CHILD_LED", "POST_CALL_AGENT"}:
        raise ValueError("알 수 없는 도움 모드")

    goal = None
    if state["goal"] is not None:
        goal_fields = _field_map(state["goal"])
        goal_id = goal_fields["goal_id"]
        if goal_id not in GOAL_LABELS:
            raise ValueError("등록되지 않은 목표")
        slots = cast(dict[str, JsonValue], dict(goal_fields["approved_slots"]))
        goal = Goal(
            goal_id=cast(GoalId, goal_id),
            approved_slots=slots,
            source_utterance_id=goal_fields["source_utterance_id"],
            goal_revision=goal_fields["goal_revision"],
        )
    instruction = None
    if state["instruction"] is not None:
        instruction_fields = _field_map(state["instruction"])
        if instruction_fields["action"] not in _ACTIONS:
            raise ValueError("등록되지 않은 조작")
        instruction = Instruction(
            instruction_id=instruction_fields["instruction_id"],
            instruction_revision=instruction_fields["instruction_revision"],
            source_utterance_id=instruction_fields["source_utterance_id"],
            target_key=instruction_fields["target_key"],
            action=cast(Action, instruction_fields["action"]),
            origin_screen_revision=instruction_fields["origin_screen_revision"],
        )

    # A paused or ended snapshot may still expose the prior screen object.
    current_screen = state["screen"]
    if state.get("observation_health", "ACTIVE") not in {"ACTIVE", "STABLE", "stable"}:
        current_screen = None
    handoff_ready = (
        mode == "CALL_CHILD_LED"
        and state["call_state"] == "ACTIVE"
        or mode == "POST_CALL_AGENT"
        and state["call_state"] == "ENDED"
        and state["help_state"] != "ENDED"
    )
    return AgentSnapshot(
        mode=cast(Mode, mode),
        basis=run_basis,
        processing_allowed=processing_allowed and handoff_ready,
        instruction=instruction,
        goal=goal,
        screen=_screen(current_screen) if current_screen is not None else None,
        trail=_saved_trail(trail),
    )
