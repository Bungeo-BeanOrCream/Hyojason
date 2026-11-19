"""
N-16. 화면과 관찰 이력에서 안내 가능한 행동 후보를 구성한다.

입력은 화면 요약, 현재 지시 또는 목표, 방문 이력, 정책 버전, Basis다.
출력은 행동 후보 목록, 탐색 범위, 현재 화면·복귀 후보 수다.
현재 버튼과 검증된 스크롤·복귀 경로를 찾고, 정상 0건·부분 실패·오류를 구분한다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..contracts import (
    Basis,
    Candidate,
    CandidateSet,
    Goal,
    Instruction,
    Mode,
    NavigationTrail,
    NodeResult,
    ScreenState,
    StepDiagnostic,
    TrailEntry,
)
from ._execution import ExecutionContext, NodeRun
from .profiles import (
    GOAL_ROUTES,
    POLICY_REVISION,
    alarm_token,
    arrival_evidence,
    below_fold_evidence,
    is_dangerous,
    is_protected,
    is_stable,
    required_arrival_evidence,
    target_profile,
)


@dataclass(frozen=True, kw_only=True)
class EnumerateActionCandidatesInput:
    mode: Mode
    screen: ScreenState
    instruction: Instruction | None
    goal: Goal | None
    trail: NavigationTrail
    policy_revision: int
    basis: Basis
    execution: ExecutionContext | None = None
    recovery_only: bool = False


class EnumerateActionCandidates:
    node_id = "N-16"

    def run(self, request: EnumerateActionCandidatesInput) -> NodeResult[CandidateSet]:
        run = NodeRun(self.node_id, request.basis, request.execution)
        screen, basis = request.screen, request.basis
        if request.mode not in ("CALL_CHILD_LED", "POST_CALL_AGENT"):
            return run.fail("INVALID_INPUT", "validate")
        live = request.mode == "CALL_CHILD_LED"
        intent = request.instruction if live else request.goal
        if intent is None or (request.recovery_only and not live):
            return run.fail("INVALID_INPUT", "validate")
        if (
            screen.projection_epoch != basis.projection_epoch
            or screen.screen_revision != basis.screen_revision
            or (
                live
                and request.instruction
                and request.instruction.instruction_revision
                != basis.instruction_revision
            )
            or (
                not live
                and request.goal
                and request.goal.goal_revision != basis.goal_revision
            )
            or request.policy_revision != POLICY_REVISION
        ):
            return run.fail("STALE_BASIS", "validate")
        run.ok("validate")
        key = request.instruction.target_key if live and request.instruction else None
        if is_protected(screen) or (key and is_dangerous(key)):
            run.ok(
                "policy",
                code="PROTECTED_SCREEN"
                if is_protected(screen)
                else "DISALLOWED_ACTION",
            )
            return run.finish(
                "policy_denied",
                CandidateSet(
                    candidates=(),
                    coverage="policy_blocked",
                    active_count=0,
                    recovery_count=0,
                ),
            )
        if not is_stable(screen):
            return run.fail("DEPENDENCY_UNAVAILABLE", "visible_scan")

        visible: tuple[Candidate, ...] = ()
        below: tuple[Candidate, ...] = ()
        if not request.recovery_only:
            if live and request.instruction:
                visible, below = _instruction_candidates(request.instruction, screen)
            elif request.goal:
                visible = _goal_candidates(request.goal, screen)
        run.ok("visible_scan", count=len(visible))
        run.ok("below_fold", count=len(below))

        recovery: Candidate | None = None
        incomplete = False
        if request.recovery_only and request.instruction:
            anchor = request.instruction.origin_screen_revision
            if anchor is None:
                incomplete = True
            else:
                recovery, incomplete = _recover(
                    request.trail, screen, anchor=anchor, goal=None
                )
        elif not live and request.goal and required_arrival_evidence(request.goal):
            recovery, incomplete = _recover(
                request.trail, screen, anchor=None, goal=request.goal
            )
        else:
            run.skip("recovery_scan")

        if incomplete:
            run.steps["recovery_scan"] = StepDiagnostic(
                status="failed",
                code="CAPTURE_GAP"
                if request.trail.gap_after_seq is not None
                else "SEARCH_INCOMPLETE",
            )
            if request.recovery_only:
                return run.fail("SEARCH_INCOMPLETE", "recovery_scan")
            direct_taps = tuple(choice for choice in visible if choice.action == "TAP")
            return run.finish(
                "search_incomplete",
                CandidateSet(
                    candidates=direct_taps,
                    coverage="partial",
                    active_count=len(direct_taps),
                    recovery_count=0,
                ),
                status="partial",
            )
        if request.recovery_only or not live:
            run.ok("recovery_scan", count=int(recovery is not None))
        active = (*visible, *below)
        candidates = (*active, recovery) if recovery else active
        return run.finish(
            "candidates_found" if candidates else "no_candidates",
            CandidateSet(
                candidates=candidates,
                coverage="complete",
                active_count=len(active),
                recovery_count=int(recovery is not None),
            ),
        )


def _instruction_candidates(
    instruction: Instruction, screen: ScreenState
) -> tuple[tuple[Candidate, ...], tuple[Candidate, ...]]:
    target = instruction.target_key
    if not target or instruction.action != "TAP":
        return (), ()
    profile = target_profile(target, screen.screen_kind)
    if profile is None or profile.action != "TAP":
        return (), ()
    if (
        target == "sms.send"
        and "sms_draft:demo_recipient:demo_message" not in screen.evidence_codes
    ):
        return (), ()
    if target in screen.target_ids:
        return (
            Candidate(
                choice_id=f"tap:{target}",
                action="TAP",
                target_id=target,
                label_key=target,
                evidence_codes=("visible_target",),
                path_distance=None,
            ),
        ), ()
    evidence = below_fold_evidence(screen, target)
    if evidence:
        return (), (
            Candidate(
                choice_id=f"scroll_down:{target}",
                action="SCROLL_DOWN",
                target_id=target,
                label_key=target,
                evidence_codes=evidence,
                path_distance=None,
            ),
        )
    return (), ()


def _goal_candidates(goal: Goal, screen: ScreenState) -> tuple[Candidate, ...]:
    route = GOAL_ROUTES.get(goal.goal_id, ())
    if not required_arrival_evidence(goal) or arrival_evidence(goal, screen):
        return ()
    options: list[Candidate] = []
    evidence = screen.evidence_codes
    for index, target in enumerate(route):
        profile = target_profile(target, screen.screen_kind)
        if profile is None or target not in screen.target_ids:
            continue
        if target in {"clock.time_picker", "clock.save_alarm"}:
            edit_time_set = f"alarm_edit:{alarm_token(goal.approved_slots)}" in evidence
            if edit_time_set != (target == "clock.save_alarm"):
                continue
        if target in {"sms.text_field", "sms.send"}:
            draft_set = "sms_draft:demo_recipient:demo_message" in evidence
            if draft_set != (target == "sms.send"):
                continue
        if (
            target == "maps.destination_field"
            and "maps.demo_place_result" in screen.target_ids
        ):
            continue
        options.append(
            Candidate(
                choice_id=f"{profile.action.lower()}:{target}",
                action=profile.action,
                target_id=target,
                label_key=target,
                evidence_codes=("visible_target",),
                path_distance=len(route) - index,
            )
        )
    return tuple(options)


def _recover(
    trail: NavigationTrail,
    screen: ScreenState,
    *,
    anchor: int | None,
    goal: Goal | None,
) -> tuple[Candidate | None, bool]:
    if anchor == screen.screen_revision:
        return None, False
    gap = trail.gap_after_seq
    ordered = sorted(trail.entries, key=lambda edge: edge.observed_seq, reverse=True)
    predecessors: dict[int, list[TrailEntry]] = {}
    for edge in ordered[:20]:
        if (
            edge.reversible
            and edge.projection_epoch == screen.projection_epoch
            and edge.from_screen.projection_epoch == screen.projection_epoch
            and edge.to_screen.projection_epoch == screen.projection_epoch
            and edge.from_screen.screen_revision < edge.to_screen.screen_revision
            and is_stable(edge.from_screen)
            and is_stable(edge.to_screen)
            and (gap is None or edge.observed_seq > gap)
        ):
            predecessors.setdefault(edge.to_screen.screen_revision, []).append(edge)

    queue: deque[tuple[ScreenState, tuple[TrailEntry, ...]]] = deque([(screen, ())])
    visited = {screen.screen_revision}
    options: list[tuple[int, int, int]] = []
    while queue:
        current, path = queue.popleft()
        for edge in predecessors.get(current.screen_revision, ()):
            previous = edge.from_screen
            if (
                edge.to_screen.screen_kind != current.screen_kind
                or previous.screen_revision in visited
            ):
                continue
            if path and edge.observed_seq >= path[-1].observed_seq:
                continue
            visited.add(previous.screen_revision)
            next_path = (*path, edge)
            first_seq = next_path[0].observed_seq
            if previous.screen_revision == anchor:
                options.append((len(next_path), -first_seq, first_seq))
            elif goal is not None:
                # The D trail retains stable screen refs, not old target lists.
                # Reobserve the screen after BACK before proposing any button.
                route = GOAL_ROUTES.get(goal.goal_id, ())
                distances = [
                    len(route) - index
                    for index, target in enumerate(route)
                    if target_profile(target, previous.screen_kind)
                ]
                if distances:
                    options.append(
                        (len(next_path) + min(distances), -first_seq, first_seq)
                    )
            queue.append((previous, next_path))

    if not options:
        return None, gap is not None or trail.history_truncated or len(ordered) > 20
    distance, _, first_seq = min(options)
    return Candidate(
        choice_id=f"back:{first_seq}",
        action="BACK",
        target_id=None,
        label_key="back",
        evidence_codes=("verified_reverse_edge",),
        path_distance=distance,
    ), False
