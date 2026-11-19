"""Run the B → D → C → parent WebSocket path with synthetic input."""

from __future__ import annotations

import asyncio
from itertools import count
import os

import pytest
from fastapi.testclient import TestClient

from hyojason_voice.worker import REQUIRED_ENV
from server.agent.b05_workflow import B05Workflow
from server.agent.infer_zoro_choice import InferZoroChoice
from server.agent.tests.test_b05 import fake_response
from server.mvp import create_app


@pytest.fixture(autouse=True)
def disable_voice_keys(monkeypatch):
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def model():
    live_model = os.environ.get("HYOJASON_INTEGRATION_LIVE_MODEL") == "1"

    def decide(body, _):
        choices = body["request"]["questions"]["decision"]["criteria"]
        for selection in ("tap:settings.display", "CLOCK_ALARM", "tap:clock.add_alarm", "ARRIVED", "CORRECT"):
            if selection in choices:
                return fake_response(body, selection)
        raise AssertionError("unexpected fixture choices")

    return InferZoroChoice.from_environment() if live_model else InferZoroChoice(
        model_revision="fixture-ref", transport=decide,
    )


@pytest.mark.parametrize("assisted_role", ["PARENT", "CHILD"])
@pytest.mark.parametrize("caller_role", ["PARENT", "CHILD"])
def test_speech_uses_the_rtc_session_and_guides_only_the_assisted_device(model, assisted_role, caller_role):
    app = create_app(B05Workflow(infer_zoro_choice=model.run), fixture=True)
    hub = app.state.help_hub
    with TestClient(app) as client:
        invite = client.post("/v1/pairs/invite", json={"role": "PARENT"}).json()
        claim = client.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": invite["invite_code"]}).json()
        parent = {"Authorization": f"Bearer {invite['token']}"}
        child = {"Authorization": f"Bearer {claim['token']}"}
        assisted, helper = (parent, child) if assisted_role == "PARENT" else (child, parent)
        caller, callee = (parent, child) if caller_role == "PARENT" else (child, parent)
        helper_role = "CHILD" if assisted_role == "PARENT" else "PARENT"
        for revision, headers in enumerate((parent, child)):
            response = client.post("/v1/consent", headers=headers, json={
                "enabled": True, "consent_revision": revision, "device_capabilities": ["MIC"],
            })
            assert response.status_code == 200, response.text

        with client.websocket_connect("/v1/events", headers=assisted) as pws, \
                client.websocket_connect("/v1/events", headers=helper) as cws:
            started = client.post("/v1/calls", headers=caller, json={"assisted_role": assisted_role}).json()
            call_id = started["data"]["call_id"]
            current = hub.control_for(call_id)
            assert current.trace_id == started["trace_id"]
            assert current.snapshot.assisted_role == assisted_role
            assert client.post(f"/v1/calls/{call_id}/accept", headers=caller).status_code == 403
            assert client.post(f"/v1/calls/{call_id}/accept", headers=callee).status_code == 200
            for headers in (parent, child):
                assert client.post(f"/v1/dev/calls/{call_id}/joined", headers=headers).status_code == 200

            ids = count(1)

            def collect(ws):
                ping = f"p{next(ids)}"
                ws.send_json({"type": "ping", "event_id": ping})
                messages = []
                while (message := ws.receive_json()).get("event_id") != ping:
                    messages.append(message)
                return messages

            caller_ws, callee_ws = (pws, cws) if caller_role == assisted_role else (cws, pws)
            assert not any(e["type"] == "call.invite" for e in collect(caller_ws))
            assert sum(e["type"] == "call.invite" for e in collect(callee_ws)) == 1
            assert current.snapshot.call_state == "ACTIVE"

            def observe(kind, payload):
                event_id = f"e{next(ids)}"
                pws.send_json({"v": 1, "type": kind, "event_id": event_id, "pair_id": invite["pair_id"],
                               "call_id": call_id, "help_id": current.snapshot.help_id, "payload": payload})
                events = collect(pws)
                assert any(e["type"] == "event.ack" and e["event_id"] == event_id for e in events), events
                # 판단이 ACK 대기 중에 끝나면 결과 이벤트가 여기 섞인다. 호출자가 함께 보게 돌려준다.
                return events

            observe("projection.started", {"projection_epoch": "fixture-epoch"})
            observe("screen.observed", {"projection_epoch": "fixture-epoch", "screen_revision": 1,
                                        "observation_health": "ACTIVE", "screen_kind": "settings_home",
                                        "target_ids": ["settings.display"], "evidence_codes": []})
            final = client.post("/v1/dev/speech-final", headers=helper, json={
                "call_id": call_id, "speaker_role": helper_role, "text": "디스플레이 눌러 줘",
            })
            assert final.status_code == 200, final.text

            async def drain():
                async with asyncio.timeout(5):
                    while hub.tasks:
                        await asyncio.gather(*hub.tasks)

            client.portal.call(drain)
            events = collect(pws)
            assert any(e["type"] == "instruction.accepted" for e in events), events
            decision = next(e for e in events if e["type"] == "assist.decision")
            assert decision["payload"]["target"]["target_id"] == "settings.display", decision
            assert decision["payload"]["trace_id"] == started["trace_id"]
            assert decision["payload"]["target"]["model_revision"] == model.model_revision
            assert not {"assist.decision", "instruction.accepted"}.intersection(e["type"] for e in collect(cws))
            observe("guide.shown", {"decision_id": decision["payload"]["decision_id"], "mode": "CALL_CHILD_LED"})
            shown = [e["payload"]["goal_context"] for e in collect(cws)
                     if e["type"] == "help.view" and "goal_context" in e["payload"]]
            assert shown[-1] == {"goal_id": None, "guide_shown": True,
                                 "arrival_suspected": False, "mode": "CALL_CHILD_LED"}

            goal = client.post("/v1/dev/speech-final", headers=helper, json={
                "call_id": call_id, "speaker_role": helper_role, "text": "내일 오전 6시로 알람 맞추자. 시계 앱을 눌러 줘",
            })
            assert goal.status_code == 200, goal.text
            client.portal.call(drain)
            assert current.snapshot.goal.goal_id == "CLOCK_ALARM"
            goal_context = [e["payload"]["goal_context"] for e in collect(cws)
                            if e["type"] == "help.view" and "goal_context" in e["payload"]]
            assert goal_context[-1] == {"goal_id": "CLOCK_ALARM", "guide_shown": False,
                                        "arrival_suspected": False, "mode": "CALL_CHILD_LED"}
            original_help = current.snapshot.help_id
            assert client.post(f"/v1/calls/{call_id}/end", headers=helper).status_code == 200
            assert current.snapshot.mode == "POST_CALL_AGENT"
            assert current.snapshot.help_id == original_help
            assert client.post("/v1/calls", headers=helper).status_code == 409
            assert hub.current_control(invite["pair_id"]) is current

            observed = observe("screen.observed", {"projection_epoch": "fixture-epoch", "screen_revision": 2,
                                                   "observation_health": "ACTIVE", "screen_kind": "clock_alarm_list",
                                                   "target_ids": ["clock.add_alarm"], "evidence_codes": []})
            client.portal.call(drain)
            guide = next(e for e in [*observed, *collect(pws)]
                         if e["type"] == "assist.decision" and e["payload"]["guide"])
            assert guide["payload"]["guide"]["target_id"] == "clock.add_alarm", guide

            observe("screen.observed", {"projection_epoch": "fixture-epoch", "screen_revision": 3,
                                        "observation_health": "ACTIVE", "screen_kind": "clock_alarm_list",
                                        "target_ids": ["clock.add_alarm"],
                                        "evidence_codes": ["alarm_card_active", "alarm_next:tomorrow:06:00"]})
            client.portal.call(drain)
            helper_events = collect(cws)
            assert sum(e["type"] == "arrival.suspected" for e in helper_events) == 1
            assert any(e["type"] == "help.view" and e["payload"].get("goal_context", {}).get("arrival_suspected")
                       for e in helper_events)
            cws.send_json({"v": 1, "type": "help.complete", "event_id": "help-complete", "pair_id": invite["pair_id"],
                           "call_id": call_id, "help_id": original_help, "payload": {}})
            assert any(e["type"] == "event.ack" and e["event_id"] == "help-complete" for e in collect(cws))
            assert current.snapshot.help_state == "ENDED"
            for headers in (assisted, helper):
                assert client.get("/v1/calls/current", headers=headers).status_code == 403
            renewed = client.post("/v1/pairs/invite", headers=parent, json={"role": "PARENT"}).json()
            assert renewed["pair_id"] != invite["pair_id"]
            claimed = client.post("/v1/pairs/claim", headers=child, json={
                "role": "CHILD", "invite_code": renewed["invite_code"],
            })
            assert claimed.json()["outcome"] == "paired", claimed.text


@pytest.mark.parametrize("assisted_role", ["PARENT", "CHILD"])
def test_15_8_assisted_utterance_changes_goal_through_zoro_belief(assisted_role):
    """대화 중 지원 대상의 말로 목표가 문자 → 메일로 바뀐다 (docs/small-nodes/N-15.md 15.8).

    모델에는 실제 가족 역할과 이번 도움의 관계를 함께 보낸다.
    """
    requests = []

    def decide(body, _):
        requests.append(body)
        criteria = body["request"]["questions"]["decision"]["criteria"]
        if "NONE" not in criteria:
            return fake_response(body, next(iter(criteria)))
        latest = body["request"]["state"]["conversation"][-1]["text"]
        return fake_response(body, "MAIL_OPEN" if "메일" in latest else "SMS_SEND")

    model = InferZoroChoice(model_revision="fixture-ref", transport=decide)
    app = create_app(B05Workflow(infer_zoro_choice=model.run), fixture=True)
    hub = app.state.help_hub
    with TestClient(app) as client:
        invite = client.post("/v1/pairs/invite", json={"role": "PARENT"}).json()
        claim = client.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": invite["invite_code"]}).json()
        parent = {"Authorization": f"Bearer {invite['token']}"}
        child = {"Authorization": f"Bearer {claim['token']}"}
        for revision, headers in enumerate((parent, child)):
            assert client.post("/v1/consent", headers=headers, json={
                "enabled": True, "consent_revision": revision, "device_capabilities": ["MIC"],
            }).status_code == 200
        call_id = client.post("/v1/calls", headers=parent,
                              json={"assisted_role": assisted_role}).json()["data"]["call_id"]
        control = hub.control_for(call_id)
        assert client.post(f"/v1/calls/{call_id}/accept", headers=child).status_code == 200
        for headers in (parent, child):
            assert client.post(f"/v1/dev/calls/{call_id}/joined", headers=headers).status_code == 200

        async def drain():
            async with asyncio.timeout(5):
                while hub.tasks:
                    await asyncio.gather(*hub.tasks)

        def say(headers, role, text):
            response = client.post("/v1/dev/speech-final", headers=headers, json={
                "call_id": call_id, "speaker_role": role, "text": text})
            assert response.status_code == 200, response.text
            client.portal.call(drain)

        assisted, helper = (parent, child) if assisted_role == "PARENT" else (child, parent)
        helper_role = "CHILD" if assisted_role == "PARENT" else "PARENT"
        say(assisted, assisted_role, "문자 보내는 거 좀 알려줘")
        assert control.snapshot.goal.goal_id == "SMS_SEND"
        assert control.snapshot.goal.approved_slots == {"recipient_id": "demo-recipient", "message_id": "demo-sms"}
        assert control.snapshot.instruction is None
        say(helper, helper_role, "문자 보내는 거 말하는 거지?")
        assert control.snapshot.goal_revision == 1
        say(assisted, assisted_role, "아니 문자 말고 그 메일 말이야")
        snapshot = control.snapshot
        assert (snapshot.goal.goal_id, snapshot.goal_revision) == ("MAIL_OPEN", 2)
        assert snapshot.goal.approved_slots == {"mail_id": "demo-mail"}
        assert snapshot.goal_belief.probabilities["MAIL_OPEN"] == 0.95
        assert snapshot.instruction is None
        conversation = requests[-1]["request"]["state"]["conversation"]
        assert [(turn["speaker"], turn["relation"]) for turn in conversation] == [
            (assisted_role.lower(), "assisted"), (helper_role.lower(), "helper"),
            (assisted_role.lower(), "assisted")]
        assert "아니 문자 말고" not in repr(snapshot)


def test_unpair_ends_post_call_help_and_revokes_both_connections(model):
    app = create_app(B05Workflow(infer_zoro_choice=model.run), fixture=True)
    hub = app.state.help_hub
    with TestClient(app) as client:
        invite = client.post("/v1/pairs/invite", json={"role": "PARENT"}).json()
        claim = client.post("/v1/pairs/claim", json={"role": "CHILD", "invite_code": invite["invite_code"]}).json()
        parent = {"Authorization": f"Bearer {invite['token']}"}
        child = {"Authorization": f"Bearer {claim['token']}"}
        assert client.post("/v1/pairs/unpair").status_code == 403
        with client.websocket_connect("/v1/events", headers=parent) as parent_ws, \
                client.websocket_connect("/v1/events", headers=child) as child_ws:
            call_id = client.post("/v1/calls", headers=parent).json()["data"]["call_id"]
            control = hub.control_for(call_id)
            assert client.post(f"/v1/calls/{call_id}/accept", headers=child).status_code == 200
            for revision, headers in enumerate((parent, child)):
                assert client.post(f"/v1/dev/calls/{call_id}/joined", headers=headers).status_code == 200
                updated = client.post("/v1/consent", headers=headers, json={
                    "call_id": call_id, "enabled": True, "consent_revision": revision,
                })
                assert updated.status_code == 200, updated.text
            stale = client.post("/v1/consent", headers=parent, json={
                "call_id": call_id, "enabled": False, "consent_revision": 0,
            })
            assert stale.status_code == 409
            assert control.snapshot.help_state != "ENDED"
            assert client.post("/v1/dev/speech-final", headers=child, json={
                "call_id": call_id, "speaker_role": "CHILD",
                "text": "내일 오전 6시로 알람 맞추자. 시계 앱을 눌러 줘",
            }).status_code == 200

            async def drain():
                async with asyncio.timeout(5):
                    while hub.tasks:
                        await asyncio.gather(*hub.tasks)

            client.portal.call(drain)
            assert client.post(f"/v1/calls/{call_id}/end", headers=child).status_code == 200
            assert control.snapshot.mode == "POST_CALL_AGENT"
            assert client.post("/v1/pairs/unpair", headers=child).json()["outcome"] == "unpaired"
            assert control.snapshot.help_state == "ENDED"
            assert control.snapshot.goal is None
            for headers, socket in ((parent, parent_ws), (child, child_ws)):
                assert client.get("/v1/calls/current", headers=headers).status_code == 403
                socket.send_json({"type": "ping", "event_id": "after-unpair"})
                events = []
                while (message := socket.receive_json()).get("event_id") != "after-unpair":
                    events.append(message["type"])
                assert message["type"] == "event.rejected"
                assert message["code"] == "FORBIDDEN"
                assert events.count("help.ended") == 1
