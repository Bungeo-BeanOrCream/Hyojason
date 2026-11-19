"""N-22 · resolve_arrival_or_completion — docs/small-nodes/N-22.md 최소 검증 22.1~22.5와 구현 완료 기준."""

from __future__ import annotations

import dataclasses
import threading

import pytest

from fixtures import CHILD, OTHER_PAIR, PARENT, TRACE, goal, post_call, screen, snapshot
from hyojason_session.common.decisions import ArrivalDecision
from hyojason_session.common.enums import (
    ActorRole,
    ArrivalState,
    EndReason,
    HelpCommand,
    HelpState,
    Role,
    Source,
)
from hyojason_session.common.result import Basis, Status
from hyojason_session.common.types import ActorContext
from hyojason_session.completion import (
    CompletionInput,
    CompletionLedger,
    resolve_arrival_or_completion,
)


def arrival(snap, *, arrival_id="arr-1", arrived=True, **basis_overrides):
    b = Basis(**{**vars(Basis.from_snapshot(snap)), **basis_overrides})
    return ArrivalDecision(arrival_id=arrival_id, arrived=arrived, goal_revision=b.goal_revision,
                           evidence_codes=("alarm_card_active",) if arrived else (), basis=b)


def run_arrival(ledger, snap, decision, *, help_id=None):
    return resolve_arrival_or_completion(
        CompletionInput(arrival=decision, command=None, help_id=help_id or snap.help_id,
                        goal_revision=decision.goal_revision, actor_role=ActorRole.AGENT,
                        snapshot=snap),
        actor=None, ledger=ledger, trace_id=TRACE, source=Source.FIXTURE,
    )


def run_command(ledger, snap, command, actor, *, help_id=None, actor_role=None, goal_revision=1):
    role = actor_role or ActorRole(actor.role.value)
    return resolve_arrival_or_completion(
        CompletionInput(arrival=None, command=command, help_id=help_id or snap.help_id,
                        goal_revision=goal_revision, actor_role=role, snapshot=snap),
        actor=actor, ledger=ledger, trace_id=TRACE, source=Source.FIXTURE,
    )


@pytest.fixture
def ledger():
    return CompletionLedger()


# --- 22.1 POST_CALL_AGENT의 최신 새 도착 추정 -------------------------------------


def test_22_1_new_post_call_arrival_announced_once_help_stays_active(ledger):
    snap = post_call()
    r = run_arrival(ledger, snap, arrival(snap))
    assert (r.status, r.outcome) == (Status.SUCCESS, "arrival_announced")
    assert r.data.announcement is ArrivalState.SUSPECTED and r.data.arrival_id == "arr-1"
    assert r.data.end_requested is False and r.data.end_reason is None  # 도움은 활성
    assert ledger.arrival_state_for(snap) is ArrivalState.SUSPECTED
    assert ledger.end_claim("help-1") is None


def test_22_1_invalidated_evidence_revokes_notice(ledger):
    snap = post_call()
    run_arrival(ledger, snap, arrival(snap))
    r = run_arrival(ledger, snap, arrival(snap, arrival_id="arr-2", arrived=False))
    assert (r.outcome, r.data.announcement) == ("arrival_announced", ArrivalState.REVOKED)
    assert r.data.arrival_id == "arr-1"  # 철회는 앞서 알린 도착을 가리킨다
    assert ledger.arrival_state_for(snap) is ArrivalState.REVOKED
    again = run_arrival(ledger, snap, arrival(snap, arrival_id="arr-3"))  # 다시 도착하면 다시 알림
    assert again.data.announcement is ArrivalState.SUSPECTED


def test_new_goal_hides_previous_arrival(ledger):
    snap = post_call()
    run_arrival(ledger, snap, arrival(snap))
    assert ledger.arrival_state_for(post_call(goal=goal(2))) is ArrivalState.NONE


# --- 22.2 등록 자녀의 완료 또는 부모의 중단 ----------------------------------------


@pytest.mark.parametrize(("command", "actor", "reason"), [
    (HelpCommand.COMPLETE, CHILD, EndReason.CHILD_COMPLETED),
    (HelpCommand.STOP, PARENT, EndReason.PARENT_STOPPED),
])
def test_22_2_child_complete_or_parent_stop_requests_end(ledger, command, actor, reason):
    r = run_command(ledger, snapshot(), command, actor)
    assert (r.status, r.outcome) == (Status.SUCCESS, "end_requested")
    assert (r.data.end_requested, r.data.end_reason) == (True, reason)
    assert ledger.end_claim("help-1") is reason


def test_22_2_child_can_complete_before_any_arrival(ledger):
    r = run_command(ledger, post_call(), HelpCommand.COMPLETE, CHILD)
    assert r.outcome == "end_requested"


def test_22_2_child_complete_while_listening_is_not_applicable(ledger):
    snap = snapshot(help_state=HelpState.LISTENING, goal=None, instruction=None)
    r = run_command(ledger, snap, HelpCommand.COMPLETE, CHILD, goal_revision=0)
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "NOT_APPLICABLE"
    # 부모 안내 끄기는 듣는 중에도 가능
    assert run_command(ledger, snap, HelpCommand.STOP, PARENT, goal_revision=0).outcome == "end_requested"


def test_22_2_command_scope_is_help_id_not_goal_revision(ledger):
    """자녀는 goal_revision을 모른다(N-21). 명령은 help_id로만 범위를 확인한다."""
    r = run_command(ledger, snapshot(goal=goal(3)), HelpCommand.COMPLETE, CHILD, goal_revision=1)
    assert r.outcome == "end_requested"
    assert r.diagnostics.basis.goal_revision == 1  # 입력 스코프는 기록


# --- 22.3 이미 처리한 알림/명령 ----------------------------------------------------


def test_22_3_same_arrival_twice_announces_once(ledger):
    snap = post_call()
    first = run_arrival(ledger, snap, arrival(snap))
    second = run_arrival(ledger, snap, arrival(snap))
    other_id_same_goal = run_arrival(ledger, snap, arrival(snap, arrival_id="arr-9"))
    assert first.outcome == "arrival_announced"
    assert (second.outcome, second.data.announcement) == ("duplicate_ignored", None)
    assert other_id_same_goal.outcome == "duplicate_ignored"  # 같은 목표에 이미 알림


def test_22_3_second_end_command_is_ignored(ledger):
    run_command(ledger, snapshot(), HelpCommand.COMPLETE, CHILD)
    again = run_command(ledger, snapshot(), HelpCommand.COMPLETE, CHILD)
    parent_late = run_command(ledger, snapshot(), HelpCommand.STOP, PARENT)
    for r in (again, parent_late):
        assert (r.status, r.outcome) == (Status.SUCCESS, "duplicate_ignored")
        assert r.data.end_requested is False
        assert r.data.end_reason is EndReason.CHILD_COMPLETED  # 이미 승인된 사유 안내


def test_22_3_concurrent_complete_and_stop_end_once(ledger):
    results, barrier = [], threading.Barrier(8)

    def press(cmd, actor):
        barrier.wait()
        results.append(run_command(ledger, snapshot(), cmd, actor))

    threads = [threading.Thread(target=press, args=(HelpCommand.COMPLETE, CHILD)) for _ in range(4)]
    threads += [threading.Thread(target=press, args=(HelpCommand.STOP, PARENT)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r.data.end_requested for r in results) == 1
    assert sum(r.outcome == "duplicate_ignored" for r in results) == 7


# --- 22.4 권한 위반·이전 help_id/goal_revision -------------------------------------


@pytest.mark.parametrize(("command", "actor", "actor_role"), [
    (HelpCommand.COMPLETE, PARENT, ActorRole.PARENT),  # 부모가 자녀 완료를 보냄
    (HelpCommand.STOP, CHILD, ActorRole.CHILD),  # 자녀가 부모 중단을 보냄
    (HelpCommand.COMPLETE, PARENT, ActorRole.CHILD),  # 역할 위조
    (HelpCommand.COMPLETE, ActorContext("user-x", OTHER_PAIR, Role.CHILD), ActorRole.CHILD),
    (HelpCommand.COMPLETE, None, ActorRole.AGENT),  # Agent는 종료 명령 불가
])
def test_22_4_role_violation_is_rejected(ledger, command, actor, actor_role):
    r = resolve_arrival_or_completion(
        CompletionInput(None, command, "help-1", 1, actor_role, snapshot()),
        actor=actor, ledger=ledger, trace_id=TRACE,
    )
    assert (r.status, r.outcome, r.data) == (Status.ERROR, None, None)
    assert r.diagnostics.terminal_error.code == "UNAUTHORIZED"
    assert ledger.end_claim("help-1") is None  # 현재 도움 유지


def test_22_4_arrival_from_user_is_rejected(ledger):
    snap = post_call()
    r = resolve_arrival_or_completion(
        CompletionInput(arrival(snap), None, "help-1", 1, ActorRole.CHILD, snap),
        actor=CHILD, ledger=ledger, trace_id=TRACE,
    )
    assert r.diagnostics.terminal_error.code == "UNAUTHORIZED"


def test_22_4_previous_help_id_is_stale(ledger):
    r = run_command(ledger, snapshot(help_id="help-2"), HelpCommand.COMPLETE, CHILD, help_id="help-1")
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "STALE_BASIS"
    assert ledger.end_claim("help-2") is None


@pytest.mark.parametrize("overrides", [
    {"goal_revision": 0},  # 이전 목표
    {"mode_revision": 1},  # 통화 중 모드의 결과
    {"consent_revision": 0},  # 동의 버전 변경 전
    {"screen_revision": 4},  # 이미 다른 화면
    {"projection_epoch": "epoch-0"},  # 캡처 재승인 전
])
def test_22_4_stale_arrival_basis_is_rejected(ledger, overrides):
    snap = post_call()
    r = run_arrival(ledger, snap, arrival(snap, **overrides))
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "STALE_BASIS"
    assert ledger.arrival_state_for(snap) is ArrivalState.NONE


def test_22_4_arrival_during_call_is_not_applicable(ledger):
    snap = snapshot()  # CALL_CHILD_LED
    r = run_arrival(ledger, snap, arrival(snap))
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "NOT_APPLICABLE"


def test_22_4_arrival_after_end_is_rejected(ledger):
    snap = post_call()
    run_command(ledger, snap, HelpCommand.COMPLETE, CHILD)
    r = run_arrival(ledger, snap, arrival(snap))
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "ALREADY_ENDED"


@pytest.mark.parametrize("bad", [
    lambda s: CompletionInput(None, None, "help-1", 1, ActorRole.CHILD, s),  # 둘 다 없음
    lambda s: CompletionInput(arrival(s), HelpCommand.COMPLETE, "help-1", 1, ActorRole.AGENT, s),
    lambda s: CompletionInput(dataclasses.replace(arrival(s), evidence_codes=()), None, "help-1", 1,
                              ActorRole.AGENT, s),  # 증거 없는 도착
    lambda s: CompletionInput(arrival(s, screen_revision=None), None, "help-1", 1, ActorRole.AGENT, s),
    lambda s: CompletionInput(None, "COMPLETE", "help-1", 1, ActorRole.CHILD, s),
    lambda s: CompletionInput(None, HelpCommand.COMPLETE, "help-1", -1, ActorRole.CHILD, s),
])
def test_invalid_input_is_error(ledger, bad):
    r = resolve_arrival_or_completion(bad(post_call()), actor=CHILD, ledger=ledger, trace_id=TRACE)
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "INVALID_INPUT"


# --- 22.5 경계: 알림을 받은 뒤 완료 버튼 두 번 (구현 완료 기준) -----------------------


def test_22_5_arrival_then_complete_twice_ends_once(ledger):
    snap = post_call(screen=screen(7))
    announced = run_arrival(ledger, snap, arrival(snap))
    first = run_command(ledger, snap, HelpCommand.COMPLETE, CHILD)
    second = run_command(ledger, snap, HelpCommand.COMPLETE, CHILD)

    assert announced.outcome == "arrival_announced" and announced.data.end_requested is False
    assert (first.outcome, first.data.end_requested) == ("end_requested", True)
    assert (second.outcome, second.data.end_requested) == ("duplicate_ignored", False)

    # diagnostic.basis가 호출의 입력 스코프와 일치
    assert announced.diagnostics.basis == arrival(snap).basis
    b = first.diagnostics.basis
    assert (b.pair_id, b.help_id, b.goal_revision, b.mode_revision, b.screen_revision) == (
        "pair-fixture", "help-1", 1, 2, 7)
    runs = {r.diagnostics.run_id for r in (announced, first, second)}
    assert len(runs) == 3 and {r.diagnostics.trace_id for r in (announced, first, second)} == {TRACE}
