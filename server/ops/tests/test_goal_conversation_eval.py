"""V-05 대화형 목표 평가 도구의 회귀. 모델 응답은 모두 FIXTURE다."""

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from server.agent.infer_zoro_choice import InferZoroChoice
from server.agent.tests.test_b05 import fake_response
from server.ops import goal_conversation_eval as gce


class GoalConversationEvalTests(TestCase):
    def setUp(self):
        self.samples = gce.load_samples()
        # 발화 원문 → 그 발화 뒤 기대 목표. 가상 모델이 "정답"을 고르게 한다.
        self.expected = {
            turn["text"]: goal
            for sample in self.samples
            for turn, goal in zip(sample["turns"], sample["expected_goals"])
        }
        # 슬롯이 아직 없어 목표가 비는 발화에서도 모델은 실제 목적을 고를 수 있다.
        self.expected["알람 하나 맞춰 줘"] = "CLOCK_ALARM"
        self.expected["몇 시로 맞춰 드릴까요?"] = "CLOCK_ALARM"
        self.expected["화면이 자꾸 금방 꺼져서 불편해"] = "SET_TIMEOUT"

    def model(self, choose):
        def transport(payload, timeout):
            latest = payload["request"]["state"]["conversation"][-1]["text"]
            return fake_response(payload, choose(latest))

        return InferZoroChoice(model_revision="fixture-v05", transport=transport)

    def test_v05_oracle_model_matches_every_sample(self):
        model = self.model(lambda latest: self.expected[latest] or "NONE")
        cases = gce.evaluate(self.samples, infer=model.run, source="FIXTURE")
        summary = gce.summarize(cases)
        self.assertEqual(summary["cases"], 12)
        self.assertEqual(summary["failed_cases"], [])
        self.assertEqual(summary["final_goal_matched"], 12)
        self.assertEqual(summary["turn_goal_matched"], summary["turns"])
        # 위험 지시(GC10의 첫 발화)는 모델을 부르지 않는다.
        self.assertEqual(summary["goal_calls"], summary["turns"] - 1)
        self.assertEqual(summary["inference_sources"], ["FIXTURE"])

    def test_v05_detects_model_that_never_changes_goal(self):
        first = {}

        def stubborn(latest):
            case = next(s["id"] for s in self.samples if any(t["text"] == latest for t in s["turns"]))
            first.setdefault(case, self.expected[latest] or "NONE")
            return first[case]

        cases = gce.evaluate(self.samples, infer=self.model(stubborn).run, source="FIXTURE")
        failed = gce.summarize(cases)["failed_cases"]
        for changed in ("GC01", "GC02", "GC12"):
            self.assertIn(changed, failed)
        self.assertNotIn("GC03", failed)

    def test_v05_evidence_has_no_utterance_text(self):
        model = self.model(lambda latest: self.expected[latest] or "NONE")
        cases = gce.evaluate(self.samples, infer=model.run, source="FIXTURE")
        dumped = json.dumps(gce.evidence(cases, model_revision="fixture-v05", trace_id="v05-fixture"),
                            ensure_ascii=False)
        for text in self.expected:
            self.assertNotIn(text, dumped)

    def test_v05_budget_hold_sends_no_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "ZORO_DECIDE_URL": "http://127.0.0.1:9/v1/decide", "ZORO_MODEL_REVISION": "fixture-v05",
        }), patch.object(gce, "evaluate", side_effect=AssertionError("모델을 호출하면 안 됩니다")):
            output = Path(folder) / "v05.json"
            code = gce.main(["--spend-cents", "7000", "--evidence-json", str(output)])
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
