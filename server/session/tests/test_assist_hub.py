"""B-05 판단 연결(AssistedSessionHub)을 음성 서비스 없이 세션 패키지에서 검증한다 (#27).

통화·동의·발화·화면은 합성 입력이고 Zoro-1 응답은 FIXTURE다.
"""

import asyncio
from unittest import TestCase

from hyojason_session.api.assist import AssistedSessionHub
from hyojason_session.api.catalog import APPROVED_SLOTS, TARGET_IDS, default_catalog
from hyojason_session.common.enums import Role
from hyojason_session.common.types import ActorContext
from hyojason_session.mocks import MockTokenAuthenticator
from hyojason_session.mocks.pairing import MockPairingService
from server.agent.b05_workflow import B05Workflow
from server.agent.infer_zoro_choice import InferZoroChoice
from server.agent.tests.test_b05 import fake_response

PARENT = ActorContext("parent", "pair", Role.PARENT)
CHILD = ActorContext("child", "pair", Role.CHILD)
EVIDENCE = ("visible_target", "stable_screen_transition", *(f"observed:{target}" for target in TARGET_IDS))


class _Hub(AssistedSessionHub):
    allowed = True

    def analysis_allowed(self, pair_id: str) -> bool:
        return self.allowed


class AssistHubTests(TestCase):
    def setUp(self):
        self.requests = []

        def transport(payload, timeout):
            self.requests.append(payload)
            criteria = payload["request"]["questions"]["decision"]["criteria"]
            if "NONE" in criteria:  # GOAL
                latest = payload["request"]["state"]["conversation"][-1]["text"]
                goal = "MAIL_OPEN" if "메일" in latest else "SMS_SEND" if "문자" in latest else "NONE"
                return fake_response(payload, goal)
            return fake_response(payload, next(iter(criteria)))

        model = InferZoroChoice(model_revision="fixture-assist", transport=transport)
        self.hub = _Hub(catalog=default_catalog(EVIDENCE), approved_slots=dict(APPROVED_SLOTS),
                        pairs=MockTokenAuthenticator({"parent": PARENT, "child": CHILD}),
                        pairing=MockPairingService(), workflow=B05Workflow(infer_zoro_choice=model.run))
        self.seq = {"PARENT": 0, "CHILD": 0}

    def run_call(self, scenario):
        async def main():
            queue = asyncio.Queue()
            self.hub.connect(PARENT, queue, asyncio.get_running_loop())
            control = self.hub.create_call(PARENT, Role.PARENT)
            self.hub.accept_call(CHILD, control.snapshot.call_id)
            for role in ("PARENT", "CHILD"):
                self.hub._apply(control, "consent.updated", role, {"purpose": "analysis", "enabled": True})
            await scenario(control)
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            return control, events

        return asyncio.run(main())

    async def say(self, control, role, text):
        self.seq[role] += 1
        call_id = control.snapshot.call_id
        self.hub.apply_server_event(call_id, {"type": "speech.final", "payload": {
            "utterance_id": f"{role}-{self.seq[role]}", "utterance_seq": self.seq[role],
            "speaker_role": role, "text": text, "call_id": call_id, "source": "manual_test"}})
        await self.drain()

    async def drain(self):
        async with asyncio.timeout(5):
            while self.hub.tasks:
                await asyncio.gather(*self.hub.tasks)

    def test_assisted_utterance_changes_goal_inside_session_package(self):
        async def scenario(control):
            await self.say(control, "PARENT", "문자 보내는 거 좀 알려줘")
            self.assertEqual(control.snapshot.goal.goal_id, "SMS_SEND")
            await self.say(control, "PARENT", "아니 문자 말고 그 메일 말이야")

        control, _ = self.run_call(scenario)
        snapshot = control.snapshot
        self.assertEqual((snapshot.goal.goal_id, snapshot.goal_revision), ("MAIL_OPEN", 2))
        self.assertEqual(snapshot.goal_belief.probabilities["MAIL_OPEN"], 0.95)
        self.assertIsNone(snapshot.instruction)
        self.assertNotIn("아니 문자 말고", repr(snapshot))

    def test_helper_instruction_reaches_assisted_device_as_assist_decision(self):
        async def scenario(control):
            self.hub._apply(control, "projection.started", "PARENT", {"projection_epoch": "epoch"})
            self.hub._apply(control, "screen.observed", "PARENT", {
                "projection_epoch": "epoch", "screen_revision": 1, "observation_health": "ACTIVE",
                "screen_kind": "settings_home", "target_ids": ["settings.display"], "evidence_codes": []})
            await self.drain()
            await self.say(control, "CHILD", "디스플레이 눌러 줘")

        control, events = self.run_call(scenario)
        decisions = [event for event in events if event["type"] == "assist.decision"]
        self.assertTrue(decisions, [event["type"] for event in events])
        self.assertEqual(decisions[-1]["payload"]["target"]["target_id"], "settings.display")
        self.assertEqual(control.snapshot.instruction.target_key, "settings.display")

    def test_no_model_call_without_analysis_consent(self):
        self.hub.allowed = False

        async def scenario(control):
            await self.say(control, "PARENT", "문자 보내는 거 좀 알려줘")

        control, _ = self.run_call(scenario)
        self.assertEqual(self.requests, [])
        self.assertIsNone(control.snapshot.goal)
