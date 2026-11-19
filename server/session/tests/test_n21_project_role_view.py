"""N-21 · project_role_view — docs/small-nodes/N-21.md 최소 검증 21.1~21.4와 구현 완료 기준."""

from __future__ import annotations

import json

import pytest

from fixtures import (
    CHILD,
    CHILD_FORBIDDEN_KEYS,
    OTHER_PAIR,
    PARENT,
    PARENT_FORBIDDEN_KEYS,
    TRACE,
    all_keys,
    post_call,
    snapshot,
)
from hyojason_session.common.enums import (
    ArrivalState,
    CallState,
    EndReason,
    HelpState,
    Role,
    Source,
)
from hyojason_session.common.result import Basis, Status
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.common.types import ActorContext
from hyojason_session.role_view import RoleViewInput, project_role_view
from hyojason_session.views import COMPLETION_MESSAGE_KEY, ArrivalIndicator, ParentStatus


def run(snap, actor, *, arrival=ArrivalState.NONE, online=True, latest="help-1"):
    return project_role_view(
        RoleViewInput(snapshot=snap, viewer_role=actor.role, arrival_state=arrival, online=online),
        actor=actor,
        latest_help_id=latest,
        trace_id=TRACE,
        source=Source.FIXTURE,
    )


def child_payload(result) -> dict:
    return to_jsonable(result.data)


# --- 21.1 페어 역할에 맞게 현재 상태 조립 -----------------------------------------


def test_21_1_parent_gets_mode_goal_and_stop():
    r = run(snapshot(), PARENT)
    assert (r.status, r.outcome) == (Status.SUCCESS, "view_ready")
    assert r.data.role is Role.PARENT and r.data.child_view is None
    pv = r.data.parent_view
    assert pv.status is ParentStatus.CALL_GUIDING
    assert pv.goal.goal_id == "CLOCK_ALARM" and pv.goal.goal_revision == 1
    assert pv.instruction.instruction_revision == 1
    assert pv.can_stop_guidance is True and pv.completion_message_key is None
    assert not all_keys(to_jsonable(r.data)) & PARENT_FORBIDDEN_KEYS


def test_21_1_child_gets_only_call_completion_arrival():
    r = run(snapshot(), CHILD)
    assert (r.status, r.outcome) == (Status.SUCCESS, "view_ready")
    assert r.data.parent_view is None
    payload = child_payload(r)
    assert set(payload["child_view"]) == {"call_state", "can_complete", "arrival_indicator"}
    assert payload["child_view"] == {
        "call_state": "ACTIVE", "can_complete": True, "arrival_indicator": "NONE",
    }
    assert not all_keys(payload) & CHILD_FORBIDDEN_KEYS


def test_done_criteria_same_help_from_both_sides():
    """같은 도움을 양쪽에서 조회: 목표·상태는 부모만, 자녀 완료는 가능한 동안 활성,
    도착 추정은 통화 후에만."""
    in_call = snapshot()
    after = post_call()
    for snap, arrival_visible in ((in_call, False), (after, True)):
        p = run(snap, PARENT, arrival=ArrivalState.SUSPECTED)
        c = run(snap, CHILD, arrival=ArrivalState.SUSPECTED)
        assert p.data.parent_view.goal is not None
        assert c.data.child_view.can_complete is True
        expected = ArrivalIndicator.SUSPECTED if arrival_visible else ArrivalIndicator.NONE
        assert c.data.child_view.arrival_indicator is expected


def test_arrival_during_call_is_suppressed_for_both_roles():
    r = run(snapshot(), CHILD, arrival=ArrivalState.SUSPECTED)
    assert r.data.child_view.arrival_indicator is ArrivalIndicator.NONE
    assert r.diagnostics.steps["select_fields"].code == "ARRIVAL_SUPPRESSED"
    p = run(snapshot(), PARENT, arrival=ArrivalState.SUSPECTED)
    assert p.data.parent_view.status is ParentStatus.CALL_GUIDING


def test_post_call_parent_view_drops_call_instruction_and_shows_arrival():
    p = run(post_call(), PARENT, arrival=ArrivalState.SUSPECTED)
    pv = p.data.parent_view
    assert pv.status is ParentStatus.ARRIVAL_PENDING
    assert pv.instruction is None and pv.goal.goal_revision == 1
    revoked = run(post_call(), PARENT, arrival=ArrivalState.REVOKED)
    assert revoked.data.parent_view.status is ParentStatus.POST_CALL_GUIDING


# --- 21.2 활성 통화 중 아직 지시나 목표 없음 ----------------------------------------


def test_21_2_waiting_parent_listens_child_keeps_call_ui():
    snap = snapshot(help_state=HelpState.LISTENING, goal=None, instruction=None)
    p = run(snap, PARENT)
    c = run(snap, CHILD)
    assert (p.status, p.outcome) == (Status.SUCCESS, "view_waiting")
    assert p.data.parent_view.status is ParentStatus.LISTENING
    assert p.data.parent_view.can_stop_guidance is True
    assert (c.status, c.outcome) == (Status.SUCCESS, "view_waiting")
    assert c.data.child_view.call_state is CallState.ACTIVE
    assert c.data.child_view.can_complete is False


# --- 21.3 타 페어 또는 이전 도움의 snapshot ----------------------------------------


@pytest.mark.parametrize(
    ("snap", "actor", "latest", "code"),
    [
        (snapshot(pair_id=OTHER_PAIR), PARENT, "help-1", "UNAUTHORIZED"),
        (snapshot(), ActorContext("user-x", OTHER_PAIR, Role.CHILD), "help-1", "UNAUTHORIZED"),
        (snapshot(help_id="help-old"), CHILD, "help-1", "STALE_BASIS"),
        (snapshot(), PARENT, None, "STALE_BASIS"),
    ],
)
def test_21_3_other_pair_or_previous_help_is_rejected(snap, actor, latest, code):
    r = run(snap, actor, latest=latest)
    assert (r.status, r.outcome, r.data) == (Status.ERROR, None, None)
    assert r.diagnostics.terminal_error.code == code
    assert r.diagnostics.terminal_error.step == "authorize_scope"


def test_21_3_role_spoof_is_rejected():
    # 자녀 토큰으로 부모 view를 요청
    r = project_role_view(
        RoleViewInput(snapshot(), Role.PARENT, ArrivalState.NONE, True),
        actor=CHILD, latest_help_id="help-1", trace_id=TRACE,
    )
    assert r.status is Status.ERROR and r.diagnostics.terminal_error.code == "UNAUTHORIZED"


# --- 21.4 경계: 자녀가 통화 종료 뒤 첫 화면을 엶 -------------------------------------


def test_21_4_child_opens_app_after_call_ended():
    snap = post_call()
    r = run(snap, CHILD)
    assert (r.status, r.outcome) == (Status.SUCCESS, "view_ready")
    assert to_jsonable(r.data.child_view) == {
        "call_state": "ENDED", "can_complete": True, "arrival_indicator": "NONE",
    }
    d = r.diagnostics
    assert d.basis == Basis.from_snapshot(snap)
    assert (d.basis.pair_id, d.basis.help_id, d.basis.mode_revision) == ("pair-fixture", "help-1", 2)
    assert d.trace_id == TRACE and d.node_id == "N-21" and d.source is Source.FIXTURE
    again = run(snap, CHILD)
    assert again.diagnostics.run_id != d.run_id  # 호출마다 새 run_id


# --- 종료·연결 상태 --------------------------------------------------------------


def test_child_completed_help_shows_completion_to_parent_once_source():
    snap = post_call(help_state=HelpState.ENDED, end_reason=EndReason.CHILD_COMPLETED)
    pv = run(snap, PARENT).data.parent_view
    assert pv.status is ParentStatus.COMPLETED
    assert pv.completion_message_key == COMPLETION_MESSAGE_KEY
    assert pv.goal is None and pv.can_stop_guidance is False
    cv = run(snap, CHILD, arrival=ArrivalState.SUSPECTED).data.child_view
    assert cv.can_complete is False and cv.arrival_indicator is ArrivalIndicator.NONE


def test_parent_stop_has_no_completion_message():
    snap = snapshot(help_state=HelpState.ENDED, end_reason=EndReason.PARENT_STOPPED)
    pv = run(snap, PARENT).data.parent_view
    assert pv.status is ParentStatus.STOPPED and pv.completion_message_key is None


def test_offline_disables_child_complete_but_not_parent_stop():
    assert run(snapshot(), CHILD, online=False).data.child_view.can_complete is False
    assert run(snapshot(), PARENT, online=False).data.parent_view.can_stop_guidance is True


# --- 입력 검사와 결과 봉투 --------------------------------------------------------


@pytest.mark.parametrize(
    "bad_input",
    [
        RoleViewInput(snapshot(mode_revision=-1), Role.PARENT, ArrivalState.NONE, True),
        RoleViewInput(snapshot(), "PARENT", ArrivalState.NONE, True),
        RoleViewInput(snapshot(), Role.PARENT, "SUSPECTED", True),
        RoleViewInput(snapshot(), Role.PARENT, ArrivalState.NONE, 1),
        RoleViewInput(snapshot(help_state=HelpState.ENDED), Role.PARENT, ArrivalState.NONE, True),
    ],
)
def test_invalid_input_is_error(bad_input):
    r = project_role_view(bad_input, actor=PARENT, latest_help_id="help-1", trace_id=TRACE)
    assert (r.status, r.outcome, r.data) == (Status.ERROR, None, None)
    assert r.diagnostics.terminal_error.code == "INVALID_INPUT"


def test_result_envelope_is_json_serializable_with_all_keys():
    payload = to_jsonable(run(snapshot(), PARENT))
    assert set(payload) == {"status", "outcome", "data", "diagnostics"}
    assert set(payload["diagnostics"]) == {
        "trace_id", "run_id", "node_id", "source", "basis", "input_refs",
        "duration_ms", "steps", "terminal_error",
    }
    assert len(payload["diagnostics"]["basis"]) == 11
    assert payload["diagnostics"]["basis"]["utterance_seq"] is None
    json.dumps(payload, ensure_ascii=False)
