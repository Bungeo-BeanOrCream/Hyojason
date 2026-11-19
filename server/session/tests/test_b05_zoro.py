"""B-05 연결의 실제 Zoro-1 경로 (#23: L40S 배포).

1) 설정 검사와 HTTP 경로: 로컬 가짜 /v1/decide 서버로 C의 실제 HTTP 호출(N-14 _post)을 그대로 거친다. FIXTURE.
2) 실제 모델: HYOJASON_LIVE_ZORO=1 과 ZORO_DECIDE_URL·ZORO_MODEL_REVISION(·ZORO_DECIDE_TOKEN)을 실행 환경에 주면 돈다.
   통화·발화·화면은 합성이라 결과 출처는 FIXTURE이고, 모델 호출만 실제다(결과의 model_revision으로 확인).
   값은 저장소에 쓰지 않는다. 터널 주소·리비전은 C(#23)에게 받는다.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hyojason_session.api.agent_bridge import AgentBridge
from hyojason_session.api.app import agent_from_env

from test_b05_bridge import Flow, of, types

TEXT = "화면 자동 꺼짐을 10분으로 바꿔 줘. 디스플레이 눌러 줘"


class FakeDecide(BaseHTTPRequestHandler):
    """Zoro-1 공식 /v1/decide 형식의 가짜 서버. 첫 선택지를 고르고 받은 요청을 기록한다."""

    calls: list = []
    delay = 0.0

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if self.delay:
            threading.Event().wait(self.delay)
        criteria = list(body["request"]["questions"]["decision"]["criteria"])
        rest = 0.05 / (len(criteria) - 1)
        answer = {"model_revision": "fake-rev", "answers": {"decision": {
            "type": "choice", "choice": criteria[0],
            "probabilities": {c: 0.95 if i == 0 else rest for i, c in enumerate(criteria)}}}}
        data = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 요청 내용을 콘솔에 남기지 않는다
        pass


@pytest.fixture
def decide():
    FakeDecide.calls, FakeDecide.delay = [], 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDecide)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/decide"
    server.shutdown()
    server.server_close()


def test_zoro_mode_refuses_to_start_without_endpoint_and_revision(monkeypatch):
    with pytest.raises(RuntimeError, match="ZORO_DECIDE_URL, ZORO_MODEL_REVISION"):
        AgentBridge.with_zoro_from_env(env={})
    with pytest.raises(RuntimeError, match="ZORO_MODEL_REVISION"):
        AgentBridge.with_zoro_from_env(env={"ZORO_DECIDE_URL": "http://127.0.0.1:9/v1/decide"})
    monkeypatch.delenv("ZORO_DECIDE_URL", raising=False)
    monkeypatch.delenv("ZORO_MODEL_REVISION", raising=False)
    with pytest.raises(RuntimeError, match="ZORO_DECIDE_URL"):
        agent_from_env("zoro")


def test_zoro_http_path_reaches_parent_with_model_revision(decide):
    bridge = AgentBridge.with_zoro_from_env(env={
        "ZORO_DECIDE_URL": decide, "ZORO_MODEL_REVISION": "fake-rev", "ZORO_DECIDE_TOKEN": "test-token"})
    flow = Flow(bridge=bridge)
    flow.speak(TEXT)
    parent = flow.parent.take()
    [located] = of(parent, "target.located")
    assert (located["payload"]["target_id"], located["payload"]["model_revision"]) == ("settings.display", "fake-rev")
    assert of(parent, "goal.updated")[0]["payload"]["goal_id"] == "SET_TIMEOUT"
    # 공식 형식으로 보냈고, 토큰은 헤더로만 가며, 발화 원문은 모델 요청에 없다 (C N-14 규칙)
    assert {c["path"] for c in FakeDecide.calls} == {"/v1/decide"}
    assert {c["auth"] for c in FakeDecide.calls} == {"Bearer test-token"}
    assert all(c["body"]["reasoning"] is False for c in FakeDecide.calls)
    assert "꺼짐을 10분으로" not in json.dumps([c["body"] for c in FakeDecide.calls], ensure_ascii=False)


def test_unreachable_model_keeps_instruction_and_withholds_location():
    """#23에서 Mac → VM 위치 요청이 TIMEOUT된 경우. 지시(t0)는 그대로 가고 위치 표시 근거만 보내지 않는다."""
    bridge = AgentBridge.with_zoro_from_env(env={
        "ZORO_DECIDE_URL": "http://127.0.0.1:9/v1/decide", "ZORO_MODEL_REVISION": "rev"})
    flow = Flow(bridge=bridge)
    flow.speak(TEXT)
    parent = flow.parent.take()
    assert of(parent, "instruction.accepted") and not of(parent, "target.located")
    assert not of(parent, "goal.updated")  # 목표는 모델 판단이 필요하다
    locate = [run for run in flow.runs() if run.event == "instruction.accepted"][0]
    # 닫힌 포트: 바로 거절되면 DEPENDENCY_UNAVAILABLE, 거절이 마감보다 늦게 오면(Windows) TIMEOUT
    assert (locate.status, locate.applied) == ("error", ())
    assert locate.error in ("DEPENDENCY_UNAVAILABLE", "TIMEOUT")


def test_slow_model_times_out_without_stale_display(decide):
    FakeDecide.delay = 1.5  # 질문별 마감(N-15 목표 1000ms 등)을 넘긴다
    bridge = AgentBridge.with_zoro_from_env(env={"ZORO_DECIDE_URL": decide, "ZORO_MODEL_REVISION": "fake-rev"})
    flow = Flow(bridge=bridge)
    flow.speak(TEXT)
    parent = flow.parent.take()
    assert of(parent, "instruction.accepted") and not of(parent, "target.located")
    assert {run.error for run in flow.runs()} >= {"TIMEOUT"}


live = pytest.mark.skipif(
    os.environ.get("HYOJASON_LIVE_ZORO") != "1"
    or not (os.environ.get("ZORO_DECIDE_URL") and os.environ.get("ZORO_MODEL_REVISION")),
    reason="실제 Zoro-1: HYOJASON_LIVE_ZORO=1, ZORO_DECIDE_URL, ZORO_MODEL_REVISION 필요 (#23)",
)


@live
def test_live_zoro_settings_target_reaches_parent():
    """#23 '통화 중 설정 판단'과 같은 입력을 D 연결로 재현한다. 실기기 화면 표시는 별도 검증."""
    flow = Flow(bridge=AgentBridge.with_zoro_from_env())
    flow.speak("디스플레이 눌러 줘")
    parent, child = flow.parent.take(), flow.child.take()
    runs = [(r.event, r.status, r.outcome, r.error) for r in flow.runs()]
    located = of(parent, "target.located")
    assert located and located[0]["payload"]["target_id"] == "settings.display", runs
    assert located[0]["payload"]["model_revision"] == os.environ["ZORO_MODEL_REVISION"]
    assert types(parent).index("instruction.accepted") < types(parent).index("target.located")
    flow.assert_child_clean(child)


@live
def test_live_zoro_post_call_arrival_reaches_child():
    flow = Flow(bridge=AgentBridge.with_zoro_from_env())
    flow.speak(TEXT)
    flow.observe("settings_display", 2, ["settings.screen_timeout"], ["navigation_transition"], after=1)
    flow.hub.apply_server_event(flow.call, {"type": "call.ended"})
    flow.observe("settings_screen_timeout", 3, ["settings.timeout.10m"], ["timeout_10m_selected"])
    runs = [(r.event, r.status, r.outcome, r.error, r.applied) for r in flow.runs()]
    assert of(flow.child.take(), "arrival.suspected"), runs
