"""B-05 연결 — D 세션 서버 → C B05Workflow → D(N-11·N-22) → 지원 대상 기기 (#14 다음 계획 3).

SessionHub에 가짜 WS 연결(받은 메시지를 쌓는 상자)을 붙이고, B-02 대신 speech.final을 서버 내부 이벤트로 넣는다.
C 코드는 그대로 쓰고 Zoro-1만 MOCK(fixture_zoro: 첫 선택지를 0.95로 고름)이다. 결과는 모두 FIXTURE.
'navigation_transition'은 A의 N-09 탐지 근거 코드가 정해지기 전까지 쓰는 시험용 근거다 (catalog.py).
"""

from __future__ import annotations

import itertools

import pytest

from hyojason_session.api.agent_bridge import RESULT_EVENTS, AgentBridge
from hyojason_session.api.catalog import APPROVED_SLOTS, default_catalog
from hyojason_session.api.hub import SessionHub
from hyojason_session.api.wire import COUNTERPART_DELIVERABLE
from hyojason_session.common.enums import Role
from hyojason_session.common.types import ActorContext
from hyojason_session.mocks import MockPairingService, MockTokenAuthenticator

PARENT = ActorContext("parent", "pair", Role.PARENT)
CHILD = ActorContext("child", "pair", Role.CHILD)
_ids = itertools.count(1)

# 자녀에게 나가면 안 되는 키 (N-21: 목표·지시·화면·버전·이력)
CHILD_FORBIDDEN = {
    "goal", "goal_id", "approved_slots", "instruction", "target_key", "target_id", "screen", "screen_kind",
    "target_ids", "evidence_codes", "basis", "mode", "verdict", "location_kind",
}


class Inbox:
    """WS 송신 큐 자리. hub가 넣은 메시지를 순서대로 쌓는다."""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def put_nowait(self, message: dict) -> None:
        self.items.append(message)

    def take(self) -> list[dict]:
        out, self.items = self.items, []
        return out


class ImmediateLoop:
    def call_soon_threadsafe(self, fn, *args) -> None:
        fn(*args)


def types(messages) -> list[str]:
    return [m["type"] for m in messages]


def of(messages, kind) -> list[dict]:
    return [m for m in messages if m["type"] == kind]


def keys(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(keys(v) for v in value)) if value else set()
    return set()


class Flow:
    """부모 발신 → 자녀 수락 → 양쪽 동의 → 부모 캡처 시작 → 설정 홈 관찰까지 진행한 통화."""

    def __init__(self, *, prefer=(), threaded=False, bridge=None) -> None:
        self.bridge = bridge or AgentBridge.with_fixture_model(prefer=prefer, threaded=threaded)
        self.hub = SessionHub(
            catalog=default_catalog(extra_evidence=("navigation_transition",)), approved_slots=APPROVED_SLOTS,
            pairs=MockTokenAuthenticator({"p": PARENT, "c": CHILD}), pairing=MockPairingService(),
            agent=self.bridge,
        )
        self.parent, self.child = Inbox(), Inbox()
        self.hub.connect(PARENT, self.parent, ImmediateLoop())
        self.hub.connect(CHILD, self.child, ImmediateLoop())
        self.control = self.hub.create_call(PARENT)
        self.call = self.control.snapshot.call_id
        self.hub.accept_call(CHILD, self.call)
        for actor in (PARENT, CHILD):
            self.send(actor, "consent.updated", {"purpose": "analysis", "enabled": True})
        self.send(PARENT, "projection.started", {"projection_epoch": "capture-1"})
        self.observe("settings_home", 1, ["settings.display"])  # 지시 전 관찰: B-05는 waiting
        self.parent.take(), self.child.take()
        self.bridge.results.purge(self.help)  # 준비 단계 기록은 시험 대상이 아니다

    @property
    def help(self) -> str:
        return self.control.snapshot.help_id

    def send(self, actor, kind, payload=None) -> dict:
        reply = self.hub.handle_client(actor, {
            "v": 1, "type": kind, "event_id": f"e{next(_ids)}", "pair_id": "pair", "call_id": self.call,
            "help_id": self.help, "payload": payload or {}})
        assert reply["type"] == "event.ack", reply
        return reply

    def observe(self, kind, revision, targets=(), evidence=(), *, after=None) -> dict:
        payload = {"projection_epoch": "capture-1", "screen_revision": revision, "observation_health": "ACTIVE",
                   "screen_kind": kind, "target_ids": list(targets), "evidence_codes": list(evidence)}
        if after is not None:
            payload |= {"from_screen_revision": after, "reversible": True}
        return self.send(PARENT, "screen.observed", payload)

    def speak(self, text, seq=1, role="CHILD"):
        result = self.hub.apply_server_event(self.call, {"type": "speech.final", "payload": {
            "speaker_role": role, "utterance_id": f"t{seq}", "utterance_seq": seq, "text": text,
            "source": "FIXTURE"}})
        assert result.status == "success", result.diagnostics.terminal_error
        return result

    def runs(self):
        return self.bridge.results.of(self.help)

    def assert_child_clean(self, messages):
        assert set(types(messages)) <= COUNTERPART_DELIVERABLE
        assert not keys([m.get("payload", {}) for m in messages]) & CHILD_FORBIDDEN


def test_B05_2_child_turn_drafts_instruction_and_goal_then_locates_target():
    flow = Flow()
    flow.speak("화면 자동 꺼짐을 10분으로 바꿔 줘. 디스플레이 눌러 줘")
    parent, child = flow.parent.take(), flow.child.take()

    # 지시가 목표·위치보다 먼저 부모에게 간다 (부모 t0는 위치 탐색을 기다리지 않음)
    kinds = types(parent)
    assert kinds.index("instruction.accepted") < kinds.index("goal.updated") < kinds.index("target.located")
    assert of(parent, "instruction.accepted")[0]["payload"]["target_key"] == "settings.display"
    assert of(parent, "goal.updated")[0]["payload"]["approved_slots"] == {"duration_minutes": 10}
    [located] = of(parent, "target.located")
    assert located["payload"] | {"basis": None} == {
        "location_kind": "visible", "target_id": "settings.display", "anchor_screen_revision": 1,
        "evidence_codes": ["visible_target"], "model_revision": "fixture-zoro", "basis": None}
    assert located["payload"]["basis"]["instruction_revision"] == 1
    flow.assert_child_clean(child)

    s = flow.control.snapshot
    assert (s.instruction.target_key, s.goal.goal_id) == ("settings.display", "SET_TIMEOUT")
    speech, locate = flow.runs()
    assert (speech.event, speech.applied) == (
        "speech.final", (("instruction.drafted", "state_applied"), ("goal.drafted", "state_applied")))
    assert (locate.event, locate.outcome, locate.applied) == (
        "instruction.accepted", "live_evidence_ready", (("target.located", "state_applied"),))


def test_B05_1_verified_transition_is_assessed_and_clears_instruction():
    flow = Flow()
    flow.speak("화면 자동 꺼짐을 10분으로 바꿔 줘. 디스플레이 눌러 줘")
    flow.parent.take(), flow.child.take()

    flow.observe("settings_display", 2, ["settings.screen_timeout"], ["navigation_transition"], after=1)
    parent, child = flow.parent.take(), flow.child.take()
    [assessed] = of(parent, "action.assessed")
    assert {k: assessed["payload"][k] for k in ("verdict", "from_screen_revision", "to_screen_revision")} == {
        "verdict": "CORRECT", "from_screen_revision": 1, "to_screen_revision": 2}
    assert assessed["payload"]["evidence_codes"] == ["observed:settings.display"]
    assert "instruction.cleared" in types(parent)
    assert flow.control.snapshot.instruction is None
    assert flow.runs()[-1].event == "observation.transition"
    flow.assert_child_clean(child)


def test_B05_4_post_call_guides_next_action_then_arrival_reaches_child_through_n22():
    flow = Flow()
    flow.speak("화면 자동 꺼짐을 10분으로 바꿔 줘. 디스플레이 눌러 줘")
    flow.observe("settings_display", 2, ["settings.screen_timeout"], ["navigation_transition"], after=1)
    assert flow.hub.apply_server_event(flow.call, {"type": "call.ended"}).outcome == "call_ended_help_active"
    assert flow.runs()[-1].outcome == "waiting"  # 통화 종료 직후에는 새 안정 화면을 기다린다
    flow.parent.take(), flow.child.take()

    # 통화 후 첫 관찰(같은 의미 화면이라 revision 유지): 도착 근거가 없으니 다음 행동 하나를 부모에게 제안
    flow.observe("settings_display", 2, ["settings.screen_timeout"])
    [guide] = of(flow.parent.take(), "guide.proposed")
    assert (guide["payload"]["mode"], guide["payload"]["target_id"]) == ("POST_CALL_AGENT", "settings.screen_timeout")
    assert not of(flow.child.take(), "guide.proposed")

    # 목표 화면과 상태 근거가 보이면 도착 추정 → N-22 → 자녀에게 arrival.suspected
    flow.observe("settings_screen_timeout", 3, ["settings.timeout.10m"], ["timeout_10m_selected"])
    parent, child = flow.parent.take(), flow.child.take()
    [arrival] = of(child, "arrival.suspected")
    assert set(arrival["payload"]) == {"arrival_id"}
    assert of(child, "help.view")[-1]["payload"]["child_view"]["arrival_indicator"] == "SUSPECTED"
    assert flow.control.snapshot.help_state == "ARRIVAL_PENDING"
    assert flow.runs()[-1].applied == (("arrival", "arrival_announced"),)
    flow.assert_child_clean(child)
    assert not of(parent, "arrival.suspected")


def test_parent_speech_is_not_interpreted_and_result_events_go_to_assisted_only():
    flow = Flow()
    flow.speak("디스플레이 눌러 줘", role="PARENT")  # 지원 대상 자신의 발화는 지시가 아니다 (E07)
    assert flow.runs() == () and flow.control.snapshot.instruction is None
    flow.speak("디스플레이 눌러 줘", seq=2)
    child = flow.child.take()
    assert not set(types(child)) & RESULT_EVENTS
    flow.assert_child_clean(child)


def test_help_end_purges_b05_records_and_stops_interpretation():
    flow = Flow()
    flow.speak("디스플레이 눌러 줘")
    ended = flow.help
    assert flow.runs()
    flow.send(PARENT, "help.stop")
    assert flow.bridge.results.of(ended) == ()  # N-23이 INFERENCE_RESULTS 범위를 지움
    result = flow.hub.apply_server_event(flow.call, {"type": "speech.final", "payload": {
        "speaker_role": "CHILD", "utterance_id": "t9", "utterance_seq": 9, "text": "디스플레이 눌러 줘"}})
    assert result.status == "error" and flow.bridge.results.of(ended) == ()


def test_known_gap_c_goal_slots_differ_from_d_catalog():
    """C는 SET_FONT를 {"delta": 1}로 만들고 D(goals.json·이슈 #3)는 {"steps": n}만 받는다.

    지시는 그대로 진행되고 목표 초안만 N-11이 거절해 앱에는 아무것도 가지 않는다. 슬롯 이름을 맞추면 이 테스트를 바꾼다.
    """
    flow = Flow()
    flow.speak("글자를 크게 키워 줘. 디스플레이 눌러 줘")
    parent = flow.parent.take()
    assert of(parent, "instruction.accepted") and not of(parent, "goal.updated")
    speech = flow.runs()[0]
    assert ("goal.drafted", "INVALID_INPUT") in speech.applied
    assert flow.control.snapshot.goal is None


def test_threaded_bridge_runs_off_the_caller_thread():
    flow = Flow(threaded=True)
    flow.speak("디스플레이 눌러 줘")
    assert flow.bridge.wait_idle()  # 발화 작업 중에 생긴 위치 작업까지 기다린다
    parent = flow.parent.take()
    assert types(parent).index("instruction.accepted") < types(parent).index("target.located")
    flow.bridge.close()


@pytest.mark.parametrize("text", ["", "디스플레이"])
def test_utterance_without_instruction_or_goal_changes_nothing(text):
    flow = Flow()
    flow.speak(text)
    assert of(flow.parent.take(), "instruction.accepted") == []
    assert flow.runs()[0].applied == ()
