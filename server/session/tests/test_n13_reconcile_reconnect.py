"""N-13 · reconcile_reconnect — docs/small-nodes/N-13.md 최소 검증 13.1~13.4와 구현 완료 기준.

의존 노드는 MOCK이다: N-11 상태 저장·N-22 도착 상태·N-23 종료 기록은 InMemoryHelpStore,
N-01 페어 확인은 StaticPairDirectory. 결과는 모두 source=FIXTURE.
"""

from __future__ import annotations

import dataclasses

import pytest

from fixtures import (
    CHILD,
    CHILD_FORBIDDEN_KEYS,
    CHILD_USER,
    OTHER_PAIR,
    PAIR,
    PARENT,
    PARENT_FORBIDDEN_KEYS,
    PARENT_USER,
    TRACE,
    all_keys,
    goal,
    post_call,
    screen,
    snapshot,
)
from hyojason_session.client_resume_gate import ClientAction, ClientResumeGate
from hyojason_session.common.enums import (
    ArrivalState,
    EndReason,
    ObservationHealth,
    Role,
    Source,
)
from hyojason_session.common.result import Basis, Status
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import ActorContext
from hyojason_session.mocks import InMemoryHelpStore, StaticPairDirectory
from hyojason_session.reconnect import ReconnectRequest, ResumeState, reconcile_reconnect
from hyojason_session.views import ArrivalIndicator

PAIRS = StaticPairDirectory({
    PAIR: {PARENT_USER: Role.PARENT, CHILD_USER: Role.CHILD},
    OTHER_PAIR: {"user-other-parent": Role.PARENT},
})


@pytest.fixture
def store() -> InMemoryHelpStore:
    s = InMemoryHelpStore()
    s.put_snapshot(snapshot())
    return s


def req(role=Role.PARENT, help_id="help-1", last_event_seq=10, pair_id=PAIR):
    return ReconnectRequest(pair_id=pair_id, help_id=help_id, role=role,
                            last_event_seq=last_event_seq, snapshot_request_id="snap-req-1")


def reconnect(store, request, actor=None, pairs=PAIRS):
    actor = actor or (PARENT if request.role is Role.PARENT else CHILD)
    return reconcile_reconnect(request, actor=actor, pairs=pairs, store=store,
                               trace_id=TRACE, source=Source.FIXTURE)


# --- 13.1 동일 도움·정상 역할로 재접속 ---------------------------------------------


def test_13_1_parent_resumes_with_goal_and_must_reobserve(store):
    r = reconnect(store, req(Role.PARENT))
    assert (r.status, r.outcome) == (Status.SUCCESS, "snapshot_ready")
    d = r.data
    assert d.resume_state is ResumeState.RESUMED
    assert d.must_reobserve is True
    assert d.server_event_seq == 10
    snap = d.snapshot
    assert (snap.role, snap.help_id, snap.call_id) == (Role.PARENT, "help-1", "call-1")
    assert snap.child_view is None
    assert snap.parent_view.goal.goal_revision == 1
    assert snap.parent_view.instruction.instruction_id == "instr-1"
    # 과거 Overlay·GuideProposal·화면·좌표를 재생하지 않는다 (B04.4)
    assert not all_keys(to_jsonable(snap)) & PARENT_FORBIDDEN_KEYS


def test_13_1_child_gets_only_arrival_and_completion_scope(store):
    r = reconnect(store, req(Role.CHILD))
    assert (r.status, r.outcome) == (Status.SUCCESS, "snapshot_ready")
    assert r.data.must_reobserve is False
    assert r.diagnostics.steps["reobserve_decision"].code == "NOT_APPLICABLE"
    payload = to_jsonable(r.data.snapshot)
    assert payload["parent_view"] is None
    assert set(payload["child_view"]) == {"call_state", "can_complete", "arrival_indicator"}
    assert not all_keys(payload) & CHILD_FORBIDDEN_KEYS


def test_13_1_child_sees_arrival_only_after_call(store):
    store.put_snapshot(post_call())
    store.set_arrival_state("help-1", ArrivalState.SUSPECTED)
    r = reconnect(store, req(Role.CHILD, last_event_seq=12))
    assert r.data.snapshot.child_view.arrival_indicator is ArrivalIndicator.SUSPECTED
    assert r.data.snapshot.child_view.can_complete is True


# --- 13.2 도움이 끝났거나 서버 재시작으로 상태 없음 ----------------------------------


def test_13_2_ended_help_returns_no_active_help(store):
    store.end_help("help-1", EndReason.CHILD_COMPLETED)  # N-23처럼 내용 삭제, 기록만 남김
    r = reconnect(store, req(Role.PARENT))
    assert (r.status, r.outcome) == (Status.SUCCESS, "no_active_help")
    assert r.data.snapshot is None and r.data.must_reobserve is False
    assert r.data.resume_state is ResumeState.ENDED
    assert r.data.server_event_seq == 11


def test_13_2_server_restart_without_state_is_unknown(store):
    restarted = store.restart()  # 영속 복구 없음
    r = reconnect(restarted, req(Role.PARENT))
    assert (r.status, r.outcome) == (Status.SUCCESS, "no_active_help")
    assert r.data.resume_state is ResumeState.UNKNOWN
    assert r.data.snapshot is None and r.data.server_event_seq == 0


def test_13_2_server_restart_with_recoverable_help_resumes(store):
    restarted = store.restart(keep_help_ids=("help-1",))
    r = reconnect(restarted, req(Role.PARENT))
    assert (r.outcome, r.data.resume_state, r.data.must_reobserve) == (
        "snapshot_ready", ResumeState.RESUMED, True,
    )


def test_13_2_no_help_on_either_side():
    r = reconnect(InMemoryHelpStore(), req(Role.CHILD, help_id=None, last_event_seq=0))
    assert (r.outcome, r.data.resume_state) == ("no_active_help", ResumeState.NO_HELP)


# --- 13.3 권한·토큰·역할 불일치 ----------------------------------------------------


@pytest.mark.parametrize(
    ("request_", "actor"),
    [
        (req(Role.PARENT), CHILD),  # 자녀 토큰으로 부모 snapshot 요청
        (req(Role.PARENT), ActorContext(PARENT_USER, OTHER_PAIR, Role.PARENT)),  # 토큰의 페어 불일치
        (req(Role.PARENT), ActorContext("user-removed", PAIR, Role.PARENT)),  # 승인된 페어 밖 사용자
    ],
)
def test_13_3_role_or_token_mismatch_is_error(store, request_, actor):
    r = reconnect(store, request_, actor=actor)
    assert (r.status, r.outcome, r.data) == (Status.ERROR, None, None)
    assert r.diagnostics.terminal_error.code == "UNAUTHORIZED"
    assert r.diagnostics.terminal_error.step == "authenticate"


def test_13_3_help_id_of_another_pair_is_error(store):
    store.put_snapshot(snapshot(pair_id=OTHER_PAIR, help_id="help-other"))
    store.put_snapshot(snapshot())  # 우리 페어의 최신 도움은 그대로
    r = reconnect(store, req(Role.PARENT, help_id="help-other"))
    assert r.status is Status.ERROR and r.data is None
    assert r.diagnostics.terminal_error.code == "UNAUTHORIZED"


@pytest.mark.parametrize("bad", [
    req(last_event_seq=-1), req(last_event_seq=True), req(help_id=""), req(pair_id=""),
    dataclasses.replace(req(), snapshot_request_id=""), dataclasses.replace(req(), role="PARENT"),
])
def test_13_3_invalid_request_is_error(store, bad):
    r = reconcile_reconnect(bad, actor=PARENT, pairs=PAIRS, store=store, trace_id=TRACE)
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "INVALID_INPUT"


# --- 13.4 경계: 오프라인 사이 goal_revision 증가 (구현 완료 기준) -----------------------


def test_13_4_goal_bumped_offline_late_commands_ignored_until_reobserve(store):
    # 부모 앱은 goal_revision=1, event_seq=10에서 연결이 끊겼다.
    gate = ClientResumeGate(role=Role.PARENT)
    first = reconnect(store, req(Role.PARENT))
    gate.apply_reconnect(first.data)
    gate.on_screen_observed(5)
    old_guide = Basis(**{**vars(first.diagnostics.basis), "event_seq": 11})
    assert gate.can_display(old_guide) is True  # 끊기기 전에는 유효

    # 오프라인 동안 서버에서 새 자녀 목표 채택 → goal_revision 2, event_seq 14
    store.put_snapshot(snapshot(goal=goal(2), event_seq=14))
    r = reconnect(store, req(Role.PARENT, last_event_seq=11))

    # diagnostic.basis가 호출의 입력 스코프(pair/help)와 일치하고 최신 버전을 담는다
    b = r.diagnostics.basis
    assert (b.pair_id, b.help_id, b.goal_revision, b.event_seq) == (PAIR, "help-1", 2, 14)
    # 동일 trace로 N-21 실행을 추적할 수 있다
    assert [(ref.node_id, ref.trace_id) for ref in r.diagnostics.input_refs] == [("N-21", TRACE)]
    assert r.data.snapshot.parent_view.goal.goal_revision == 2
    assert r.data.must_reobserve is True

    actions = gate.apply_reconnect(r.data)
    assert actions[:2] == [ClientAction.CLEAR_OVERLAY, ClientAction.CANCEL_TIMERS]
    assert ClientAction.REQUEST_REOBSERVE in actions

    # 늦게 도착한 goal_revision=1 명령은 무시
    late_old_goal = Basis(**{**vars(b), "goal_revision": 1, "event_seq": 15, "screen_revision": 6})
    # goal 2 기준이어도 재접속 전에 만든 제안(event_seq ≤ 14)은 재사용하지 않음
    pre_reconnect = Basis(**{**vars(b), "event_seq": 13})
    # 재관찰 전에는 최신 버전의 새 제안도 표시하지 않음
    fresh = Basis(**{**vars(b), "event_seq": 16, "screen_revision": 6})
    assert gate.can_display(late_old_goal) is False
    assert gate.can_display(pre_reconnect) is False
    assert gate.can_display(fresh) is False

    # 관찰·좌표가 새로 확인된 뒤에만 표시
    gate.on_screen_observed(6)
    assert gate.can_display(late_old_goal) is False
    assert gate.can_display(pre_reconnect) is False
    assert gate.can_display(fresh) is True


def test_client_ahead_of_restored_server_is_recorded(store):
    r = reconnect(store, req(Role.PARENT, last_event_seq=25))
    assert r.data.resume_state is ResumeState.RESUMED
    assert r.diagnostics.steps["compare_event_seq"].code == "CLIENT_AHEAD"
    assert r.data.server_event_seq == 10  # 서버 상태가 권위


# --- 오류 및 보류: 부모 일시정지 ---------------------------------------------------


@pytest.mark.parametrize("health", [ObservationHealth.STOPPED, ObservationHealth.NOT_GRANTED])
def test_paused_parent_reconnect_does_not_turn_observation_on(store, health):
    store.put_snapshot(snapshot(screen=screen(5, health)))
    r = reconnect(store, req(Role.PARENT))
    assert r.outcome == "snapshot_ready" and r.data.must_reobserve is False
    assert r.diagnostics.steps["reobserve_decision"].code == "NO_CONSENT"
    assert r.diagnostics.basis.consent_revision == 1  # 재접속이 동의 버전을 바꾸지 않음


# --- 다른 도움으로 바뀐 경우 ------------------------------------------------------


def test_switched_help_replaces_client_cache(store):
    store.end_help("help-1", EndReason.CHILD_COMPLETED)
    store.put_snapshot(snapshot(help_id="help-2", goal=None, instruction=None, event_seq=1))
    gate = ClientResumeGate(role=Role.CHILD, help_id="help-1")
    r = reconnect(store, req(Role.CHILD, help_id="help-1"))
    assert (r.outcome, r.data.resume_state) == ("snapshot_ready", ResumeState.SWITCHED)
    assert r.data.snapshot.help_id == "help-2"
    assert r.diagnostics.steps["compare_event_seq"].code == "NOT_APPLICABLE"
    assert ClientAction.CLEAR_HELP_CACHE in gate.apply_reconnect(r.data)
    assert gate.help_id == "help-2"


def test_no_active_help_clears_parent_locally(store):
    store.end_help("help-1", EndReason.PARENT_STOPPED)
    gate = ClientResumeGate(role=Role.PARENT, help_id="help-1")
    actions = gate.apply_reconnect(reconnect(store, req(Role.PARENT)).data)
    assert actions == [ClientAction.CLEAR_OVERLAY, ClientAction.CANCEL_TIMERS,
                       ClientAction.CLEAR_HELP_CACHE]
    assert gate.help_id is None
    assert gate.can_display(Basis(help_id="help-1", event_seq=99)) is False
