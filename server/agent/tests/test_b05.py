"""합성 시연·회귀 데이터에 대한 B-05 통합 검증."""

from dataclasses import replace
from unittest import TestCase

from server.agent._execution import ExecutionContext
from server.agent.b05_workflow import AgentSnapshot, B05Input, B05Workflow, RISK_WARNING
from server.agent.enumerate_action_candidate import (
    EnumerateActionCandidates,
    EnumerateActionCandidatesInput,
)
from server.agent.infer_zoro_choice import InferZoroChoice
from server.agent.session_adapter import from_session
from server.contracts import (
    Basis,
    FinalUtterance,
    Goal,
    Instruction,
    NavigationTrail,
    ScreenState,
    TrailEntry,
)


def screen(kind, revision, targets=(), *, evidence=(), health="ACTIVE"):
    return ScreenState(
        screen_kind=kind,
        screen_revision=revision,
        projection_epoch="capture",
        target_ids=targets,
        evidence_codes=evidence,
        observation_health=health,
    )


def fake_response(payload, selected, score=0.95):
    criteria = payload["request"]["questions"]["decision"]["criteria"]
    return {
        "answers": {
            "decision": {
                "type": "choice",
                "choice": selected,
                "probabilities": {
                    choice: score
                    if choice == selected
                    else (1 - score) / (len(criteria) - 1)
                    for choice in criteria
                },
            }
        }
    }


class B05FixtureTests(TestCase):
    def setUp(self):
        self.calls = []
        self.selected = ""
        self.score = 0.95
        self.before_model = lambda: None

        def transport(payload, timeout):
            self.calls.append(payload)
            self.before_model()
            return fake_response(payload, self.selected, self.score)

        self.model = InferZoroChoice(
            model_revision="fixture-revision", transport=transport
        )
        self.workflow = B05Workflow(infer_zoro_choice=self.model.run)
        self.execution = ExecutionContext(trace_id="fixture-trace", source="FIXTURE")
        self.basis = Basis(
            pair_id="pair",
            call_id="call",
            help_id="help",
            event_seq=10,
            consent_revision=2,
            goal_revision=None,
            mode_revision=1,
            instruction_revision=1,
            projection_epoch="capture",
            screen_revision=7,
            utterance_seq=1,
        )
        self.display = screen(
            "settings_display",
            7,
            ("settings.screen_timeout", "settings.display_size_text"),
        )
        self.instruction = Instruction(
            instruction_id="instruction",
            instruction_revision=1,
            source_utterance_id="turn1",
            target_key="settings.screen_timeout",
            action="TAP",
            origin_screen_revision=7,
        )
        self.goal = Goal(
            goal_id="CLOCK_ALARM",
            goal_revision=1,
            source_utterance_id="turn1",
            approved_slots={"day": "tomorrow", "time": "06:00"},
        )
        self.snapshot = AgentSnapshot(
            mode="CALL_CHILD_LED",
            basis=self.basis,
            processing_allowed=True,
            screen=self.display,
            instruction=self.instruction,
            goal=None,
            trail=NavigationTrail(entries=(), directives=(), gap_after_seq=None),
        )
        self.current = self.snapshot

    def evaluate(self, event, **kwargs):
        return self.workflow.run(
            B05Input(
                event=event, snapshot=self.snapshot, execution=self.execution, **kwargs
            ),
            read_current=lambda: self.current,
        )

    def update(self, **fields):
        self.snapshot = replace(self.snapshot, **fields)
        self.current = self.snapshot

    def observed(self, new_screen, *, reversible=True, gap=None):
        edge = TrailEntry(
            from_screen=self.snapshot.screen,
            to_screen=new_screen,
            reversible=reversible,
            projection_epoch="capture",
            evidence_codes=("navigation_transition",),
            observed_seq=11,
        )
        self.update(
            screen=new_screen,
            basis=replace(
                self.snapshot.basis,
                screen_revision=new_screen.screen_revision,
                event_seq=11,
            ),
            trail=replace(self.snapshot.trail, entries=(edge,), gap_after_seq=gap),
        )

    def hang_up(self, clock_screen):
        self.update(
            mode="POST_CALL_AGENT",
            instruction=None,
            goal=self.goal,
            screen=clock_screen,
            basis=replace(
                self.basis,
                event_seq=15,
                mode_revision=2,
                instruction_revision=None,
                screen_revision=clock_screen.screen_revision,
                goal_revision=1,
            ),
        )

    def speech(self, text, role="CHILD"):
        return FinalUtterance(
            utterance_id="turn1",
            utterance_seq=1,
            speaker_role=role,
            text=text,
            call_id="call",
            source="FIXTURE",
        )

    def test_B05_1_visible_and_correct_action(self):
        self.selected = "tap:settings.screen_timeout"
        target = self.evaluate("instruction.accepted")
        self.assertEqual(target.data.target.target_id, "settings.screen_timeout")
        self.assertEqual(target.data.target.location_kind, "visible")
        self.assertIsNone(target.data.guide)

        self.selected = "CORRECT"
        self.observed(screen("settings_screen_timeout", 8, ("settings.timeout.10m",)))
        result = self.evaluate(
            "observation.transition", before=self.display, target=target.data.target
        )
        self.assertEqual(result.data.verdict.verdict, "CORRECT")
        self.assertIsNone(result.data.recovery)

    def test_B05_1_wrong_action_recovers_and_rechecks_same_instruction(self):
        self.selected = "WRONG"
        wrong = screen("settings_display_size_text", 8)
        self.observed(wrong)
        result = self.evaluate("observation.transition", before=self.display)
        self.assertEqual(result.data.verdict.verdict, "WRONG")
        self.assertEqual(result.data.recovery.anchor_screen_revision, 7)
        self.assertEqual(result.data.recovery.path_distance, 1)
        self.assertEqual(result.data.recovery.first_edge_ref, "back:11")

        self.selected = "tap:settings.screen_timeout"
        self.observed(replace(self.display, screen_revision=9))
        resumed = self.evaluate("observation.transition", before=wrong)
        self.assertEqual(resumed.data.target.target_id, self.instruction.target_key)
        self.assertIsNone(resumed.data.verdict)

    def test_E08_gap_blocks_wrong_action_recovery(self):
        self.selected = "WRONG"
        self.observed(screen("settings_display_size_text", 8), gap=11)
        result = self.evaluate("observation.transition", before=self.display)
        self.assertIsNone(result.data.recovery)
        self.assertEqual(self.calls, [])

    def test_B05_1_below_fold_needs_container_and_target_evidence(self):
        short = replace(self.display, target_ids=())
        self.update(screen=short)
        result = self.evaluate("screen.observed")
        self.assertEqual(result.outcome, "waiting")
        self.assertFalse(self.calls)

        verified = replace(
            short,
            evidence_codes=(
                "scroll_down:settings.options",
                "below_fold:settings.options:settings.screen_timeout",
            ),
        )
        self.update(screen=verified)
        self.selected = "scroll_down:settings.screen_timeout"
        result = self.evaluate("screen.observed")
        self.assertEqual(result.data.target.location_kind, "below_fold")

    def test_E14_same_revision_new_visibility_invalidates_location(self):
        self.selected = "tap:settings.screen_timeout"
        self.before_model = lambda: setattr(
            self,
            "current",
            replace(self.snapshot, screen=replace(self.display, target_ids=())),
        )
        result = self.evaluate("screen.observed")
        self.assertEqual(result.diagnostics.terminal_error.code, "STALE_BASIS")
        self.assertIsNone(result.data)

    def test_B05_2_instruction_draft_publishes_before_goal_model(self):
        self.selected = "CLOCK_ALARM"
        early = []
        self.before_model = lambda: self.assertEqual(len(early), 1)
        result = self.workflow.run(
            B05Input(
                event="speech.final",
                snapshot=self.snapshot,
                execution=self.execution,
                speech=self.speech("내일 오전 6시로 알람 맞추자. 시계 앱을 눌러 줘"),
            ),
            read_current=lambda: self.current,
            on_instruction=early.append,
        )
        self.assertEqual(early[0].target_key, "launcher.clock")
        self.assertEqual(
            result.data.goal_draft.approved_slots, {"day": "tomorrow", "time": "06:00"}
        )
        self.assertEqual(result.diagnostics.source, "FIXTURE")
        # 대화 텍스트는 GOAL 요청에만 들어가고 결과·진단에는 남지 않는다.
        self.assertIn("내일 오전", str(self.calls))
        self.assertNotIn("내일 오전", repr(result))
        self.assertEqual(result.data.goal_belief.probabilities["CLOCK_ALARM"], 0.95)

    def test_E07_parent_utterance_and_E11_risk(self):
        self.selected = "NONE"
        parent = self.evaluate(
            "speech.final", speech=self.speech("디스플레이 눌러", "PARENT")
        )
        blocked = self.evaluate(
            "speech.final", speech=self.speech("공장 초기화 눌러 줘")
        )
        # 부모 발화는 목표 확률만 갱신한다. 지시·위치 판단은 0회.
        self.assertIsNone(parent.data.instruction_draft)
        self.assertIsNone(parent.data.goal_draft)
        self.assertIsNotNone(parent.data.goal_belief)
        self.assertEqual(self.questions(), ["GOAL"])
        self.assertEqual(blocked.data.warning, RISK_WARNING)
        self.assertTrue(blocked.data.supersedes_previous)

    def questions(self):
        return [
            "GOAL" if "NONE" in call["request"]["questions"]["decision"]["criteria"] else "OTHER"
            for call in self.calls
        ]

    def utterance(self, turn, text, role):
        return FinalUtterance(utterance_id=turn, utterance_seq=1, speaker_role=role,
                              text=text, call_id="call", source="FIXTURE")

    def test_15_8_goal_changes_from_sms_to_mail_mid_conversation(self):
        sms = Goal(goal_id="SMS_SEND", goal_revision=1, source_utterance_id="earlier",
                   approved_slots={"recipient_id": "demo-recipient", "message_id": "demo-sms"})
        self.update(goal=sms, basis=replace(self.basis, goal_revision=1))
        earlier = self.utterance("earlier", "문자 보내는 거 좀 알려줘", "PARENT")
        speech = self.utterance("turn2", "아니 문자 말고 그 메일 말이야", "PARENT")
        self.selected = "MAIL_OPEN"
        result = self.evaluate("speech.final", speech=speech, recent_utterances=(earlier,))
        self.assertEqual(result.outcome, "goal_captured")
        self.assertEqual(result.data.goal_draft.goal_id, "MAIL_OPEN")
        self.assertEqual(result.data.goal_draft.approved_slots, {"mail_id": "demo-mail"})
        self.assertIsNone(result.data.instruction_draft)
        state = self.calls[0]["request"]["state"]
        self.assertEqual([turn["text"] for turn in state["conversation"]],
                         ["문자 보내는 거 좀 알려줘", "아니 문자 말고 그 메일 말이야"])
        self.assertEqual(state["current_goal"], "SMS_SEND")

    def test_15_9_none_or_low_confidence_keeps_current_goal(self):
        self.update(goal=self.goal, basis=replace(self.basis, goal_revision=1))
        self.selected = "NONE"
        none = self.evaluate("speech.final", speech=self.speech("오늘 날씨 좋네"))
        self.assertIsNone(none.data.goal_draft)
        self.assertEqual(none.data.goal_belief.probabilities["NONE"], 0.95)
        self.selected, self.score = "SET_FONT", 0.6
        low = self.evaluate("speech.final", speech=self.speech("글씨가 좀 작나"))
        self.assertIsNone(low.data.goal_draft)
        self.selected, self.score = "CLOCK_ALARM", 0.95
        same = self.evaluate("speech.final", speech=self.speech("내일 오전 6시 알람 맞지?"))
        self.assertIsNone(same.data.goal_draft)
        missing = self.evaluate("speech.final", speech=self.speech("알람 하나 맞춰 줘"))
        self.assertIsNone(missing.data.goal_draft)

    def test_B05_3_complete_and_partial_empty_skip_model(self):
        self.update(screen=replace(self.display, target_ids=()))
        empty = self.evaluate("screen.observed")
        self.assertEqual(empty.diagnostics.steps["N-16"].code, "no_candidates")

        self.hang_up(screen("clock_alarm_list", 8))
        self.update(trail=replace(self.snapshot.trail, gap_after_seq=12))
        partial = self.evaluate("screen.observed")
        self.assertEqual(
            (partial.status, partial.outcome), ("partial", "search_incomplete")
        )
        self.assertEqual(partial.diagnostics.steps["N-17"].status, "skipped")
        self.assertFalse(self.calls)

    def test_B05_4_post_call_next_action_and_verified_arrival(self):
        self.hang_up(screen("clock_alarm_list", 8, ("clock.add_alarm",)))
        self.selected = "tap:clock.add_alarm"
        action = self.evaluate("call.ended")
        self.assertEqual(action.data.guide.target_id, "clock.add_alarm")
        self.assertEqual(action.outcome, "post_action_ready")

        incorrect = screen(
            "clock_alarm_list",
            9,
            evidence=("alarm_card_active", "alarm_next:today:06:00"),
        )
        self.update(
            screen=incorrect, basis=replace(self.snapshot.basis, screen_revision=9)
        )
        self.assertIsNone(self.evaluate("screen.observed").data.arrival)
        self.assertEqual(len(self.calls), 1)

        correct = screen(
            "clock_alarm_list",
            10,
            evidence=("alarm_card_active", "alarm_next:tomorrow:06:00"),
        )
        self.update(
            screen=correct, basis=replace(self.snapshot.basis, screen_revision=10)
        )
        self.selected = "ARRIVED"
        arrival = self.evaluate("screen.observed")
        self.assertTrue(arrival.data.arrival.arrived)
        self.assertIsNone(arrival.data.guide)
        self.assertEqual(arrival.diagnostics.steps["N-17"].status, "skipped")

    def test_B05_4_hangup_in_flight_drops_old_location(self):
        self.selected = "tap:settings.screen_timeout"
        self.before_model = lambda: setattr(
            self,
            "current",
            replace(
                self.snapshot,
                mode="POST_CALL_AGENT",
                basis=replace(self.basis, mode_revision=2),
            ),
        )
        result = self.evaluate("screen.observed")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.diagnostics.terminal_error.code, "STALE_BASIS")

    def test_B05_5_bad_model_response_and_low_confidence_are_distinct(self):
        self.selected = "invented"
        invalid = self.evaluate("screen.observed")
        self.assertEqual(
            invalid.diagnostics.terminal_error.code, "INVALID_MODEL_RESPONSE"
        )
        self.assertIsNone(invalid.data)
        self.update(
            basis=replace(self.basis, instruction_revision=2),
            instruction=replace(self.instruction, instruction_revision=2),
        )
        self.selected, self.score = "tap:settings.screen_timeout", 0.51
        unsure = self.evaluate("screen.observed")
        self.assertEqual((unsure.status, unsure.outcome), ("success", "waiting"))
        self.assertEqual(unsure.data.target.location_kind, "unknown")

    def test_N14_2_timeout_and_dedup(self):
        self.selected = "tap:settings.screen_timeout"

        def raise_timeout():
            raise TimeoutError

        self.before_model = raise_timeout
        first = self.evaluate("screen.observed")
        second = self.evaluate("screen.observed")
        self.assertEqual(first.diagnostics.terminal_error.code, "TIMEOUT")
        self.assertEqual(second.diagnostics.terminal_error.code, "TIMEOUT")
        self.assertEqual(len(self.calls), 1)

    def test_N16_3_partial_preserves_only_verified_direct_tap(self):
        current = screen("clock_alarm_list", 8, ("clock.add_alarm",))
        self.hang_up(current)
        result = EnumerateActionCandidates().run(
            EnumerateActionCandidatesInput(
                mode="POST_CALL_AGENT",
                screen=current,
                instruction=None,
                goal=self.goal,
                basis=self.snapshot.basis,
                policy_revision=1,
                trail=replace(self.snapshot.trail, gap_after_seq=12),
            )
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.data.coverage, "partial")
        self.assertEqual(result.data.active_count, 1)
        self.assertEqual(result.data.recovery_count, 0)

    def test_D_wire_snapshot_boundary(self):
        from dataclasses import asdict

        state = {
            "help_id": "help",
            "call_state": "ACTIVE",
            "help_state": "ACTIVE",
            "mode": "CALL_CHILD_LED",
            "screen": asdict(self.display),
            "instruction": asdict(self.instruction),
            "goal": None,
        }
        out = from_session(
            state,
            {"entries": [], "directives": [], "gap_after_seq": None},
            basis=asdict(self.basis),
            processing_allowed=True,
        )
        self.assertEqual(out, self.snapshot)

    def test_N14_1_official_http_payload_and_bad_probability(self):
        from io import BytesIO
        import json
        from unittest.mock import patch

        from server.agent.infer_zoro_choice import Choice, InferZoroChoiceInput

        model = InferZoroChoice(
            endpoint="http://example.invalid/v1/decide",
            model_revision="pinned-revision",
        )
        request = InferZoroChoiceInput(
            mode="CALL_CHILD_LED",
            question_kind="TARGET_LOCATION",
            deadline_ms=1000,
            context={"screen_kind": "settings_display"},
            basis=self.basis,
            execution=self.execution,
            choices=(
                Choice(choice_id="target", description="화면 자동 꺼짐"),
                Choice(choice_id="UNKNOWN", description="대상 확인 불가"),
            ),
        )
        body = b'{"answers":{"decision":{"type":"choice","choice":"target","probabilities":{"target":NaN,"UNKNOWN":0}}}}'
        with patch(
            "server.agent.infer_zoro_choice.urlopen", return_value=BytesIO(body)
        ) as http:
            result = model.run(request)
        http_request = http.call_args.args[0]
        payload = json.loads(http_request.data)
        self.assertEqual(http_request.full_url, "http://example.invalid/v1/decide")
        self.assertEqual(http_request.method, "POST")
        self.assertEqual(
            set(payload["request"]["questions"]["decision"]["criteria"]),
            {"target", "UNKNOWN"},
        )
        self.assertIs(payload["reasoning"], False)
        self.assertEqual(
            result.diagnostics.terminal_error.code, "INVALID_MODEL_RESPONSE"
        )
        self.assertEqual(result.diagnostics.source, "FIXTURE")


class GoalSlotContractTests(TestCase):
    """모델이 고른 목표의 슬롯이 D 시연 자산 형식(demo/fixtures/goals.json)과 맞는지 (#3, V-05)."""

    def test_goals_fixture_slots_match_d_catalog(self):
        import json
        from pathlib import Path

        from server.agent.interpret_child_turn import goal_slots

        fixture = Path(__file__).resolve().parents[3] / "demo" / "fixtures" / "goals.json"
        samples = json.loads(fixture.read_text(encoding="utf-8"))
        checked = 0
        for sample in samples:
            if sample["goal_id"] is None:
                continue
            with self.subTest(sample["id"]):
                self.assertEqual(goal_slots(sample["goal_id"], [sample["text"]]), sample["approved_slots"])
                checked += 1
        self.assertEqual(checked, 13)

    def test_22_korean_and_digit_clock_forms(self):
        from server.agent.interpret_child_turn import goal_slots

        cases = {
            "내일 저녁 여섯 시에 알람": "18:00",
            "내일 오전 6시로 맞춰줘": "06:00",
            "내일 아침 일곱 시 반 알람": "07:30",
            "오늘 오후 세 시 삼십오 분 알람": "15:35",
            "오늘 밤 열한 시 알람": "23:00",
            "내일 21시 알람": "21:00",
        }
        for text, time in cases.items():
            with self.subTest(text):
                self.assertEqual(goal_slots("CLOCK_ALARM", [text])["time"], time)
        # 오전·오후 없는 한글 수사나 12시 이하 숫자는 시각이 모호해 슬롯을 채우지 않는다.
        self.assertIsNone(goal_slots("CLOCK_ALARM", ["내일 여섯 시 알람"]))
        self.assertIsNone(goal_slots("CLOCK_ALARM", ["내일 6시 알람"]))

    def test_3_arrival_evidence_uses_d_asset_ids(self):
        from server.agent.profiles import required_arrival_evidence

        def goal(goal_id, slots):
            return Goal(goal_id=goal_id, goal_revision=1, source_utterance_id="turn", approved_slots=slots)

        self.assertEqual(required_arrival_evidence(goal("MAP_ROUTE", {"place": "demo-seoul-station"})),
                         ("route_shown", "route_destination:demo-seoul-station"))
        self.assertEqual(
            required_arrival_evidence(goal("SMS_SEND", {"recipient_id": "demo-recipient", "message_id": "demo-sms"})),
            ("sms_sent_status", "sms_sent:demo-recipient:demo-sms"))
        self.assertEqual(required_arrival_evidence(goal("MAIL_OPEN", {"mail_id": "demo-mail"})), ("mail_detail_open",))
        self.assertEqual(required_arrival_evidence(goal("SET_FONT", {})), ("font_scale_changed",))
        self.assertEqual(required_arrival_evidence(goal("MAP_ROUTE", {"place": "demo_place"})), ())

    def test_alarm_day_comes_from_the_time_utterance_not_small_talk(self):
        from server.agent.interpret_child_turn import goal_slots

        talk = ["엄마 내일 오전 6시로 알람 맞추자", "그래 고마워", "오늘 저녁은 드셨어요?"]
        self.assertEqual(goal_slots("CLOCK_ALARM", talk), {"day": "tomorrow", "time": "06:00"})
        split = ["내일 알람 하나 맞춰 줘", "오전 6시로"]
        self.assertEqual(goal_slots("CLOCK_ALARM", split), {"day": "tomorrow", "time": "06:00"})
        self.assertIsNone(goal_slots("CLOCK_ALARM", ["오늘 저녁은 드셨어요?", "오전 6시로"]))

    def test_3_sms_draft_evidence_uses_d_asset_ids(self):
        from server.agent.enumerate_action_candidate import _goal_candidates
        from server.agent.profiles import sms_draft_evidence

        goal = Goal(goal_id="SMS_SEND", goal_revision=1, source_utterance_id="turn",
                    approved_slots={"recipient_id": "demo-recipient", "message_id": "demo-sms"})
        targets = ("sms.text_field", "sms.send")
        empty = screen("sms_conversation", 3, targets)
        drafted = screen("sms_conversation", 4, targets, evidence=(sms_draft_evidence(),))
        old = screen("sms_conversation", 5, targets, evidence=("sms_draft:demo_recipient:demo_message",))
        self.assertEqual(sms_draft_evidence(), "sms_draft:demo-recipient:demo-sms")
        self.assertEqual([c.target_id for c in _goal_candidates(goal, empty)], ["sms.text_field"])
        self.assertEqual([c.target_id for c in _goal_candidates(goal, drafted)], ["sms.send"])
        self.assertEqual([c.target_id for c in _goal_candidates(goal, old)], ["sms.text_field"])


class IndirectInstructionTests(TestCase):
    """N-15: 버튼 이름을 직접 말하지 않는 이동 표현도 지시로 받고, 부정·잡담은 지시로 받지 않는다."""

    @staticmethod
    def target(text):
        from server.agent.interpret_child_turn import parse_instruction

        draft = parse_instruction(FinalUtterance(
            utterance_id="u", utterance_seq=1, speaker_role="CHILD", text=text, call_id="call", source="FIXTURE"))
        return (draft.target_key or "GENERIC") if draft else None

    def test_N15_indirect_navigation_phrases_name_the_target(self):
        for text, want in (
            ("디스플레이 메뉴로 들어가줘", "settings.display"),
            ("디스플레이 쪽으로 가 봐", "settings.display"),
            ("디스플레이로 이동해 줘", "settings.display"),
            ("디스플레이 탭해", "settings.display"),
            ("디스플레이 메뉴 있지 거기로 가", "settings.display"),
            ("화면 자동 꺼짐 메뉴로 가", "settings.screen_timeout"),
            ("10분으로 해 줘", "settings.timeout.10m"),
            ("디스플레이 말고 화면 자동 꺼짐으로 가", "settings.screen_timeout"),
            ("거기로 가 봐", "GENERIC"),
            ("설정 어플 들어가줘", "launcher.settings"),
            ("문자 어플 들어가", "launcher.sms"),
            ("시계 어플리케이션 열어 줘", "launcher.clock"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.target(text), want)

    def test_N15_negation_questions_and_small_talk_are_not_instructions(self):
        for text in ("디스플레이 들어가지 마", "디스플레이 말고 소리로 가", "엄마 밥 먹으러 가",
                     "디스플레이 메뉴가 어디 있어?", "화면 자동 꺼짐 하지 마", "오늘 날씨 좋다"):
            with self.subTest(text=text):
                self.assertIsNone(self.target(text))


class BelowFoldCatalogTests(TestCase):
    """B-05/N-16: the integrated server accepts the below-fold evidence a device sends for a known target."""

    def test_N16_settings_display_below_fold_codes_are_in_the_mvp_catalog(self):
        from server.agent.profiles import below_fold_evidence
        from server.mvp import below_fold_codes

        codes = ("below_fold:settings_home:settings.display", "scroll_down:settings_home")
        self.assertTrue(set(codes) <= set(below_fold_codes()))
        observed = screen("settings_home", 1, (), evidence=codes)
        self.assertEqual(below_fold_evidence(observed, "settings.display"), ("scroll_down:settings_home", codes[0]))


class InCallGoalStepTests(TestCase):
    """EXPERIMENT (#37): 통화 중 직접 지시 없이 승인된 목표만 있을 때 다음 단계 대상."""

    @staticmethod
    def goal(goal_id, slots=None):
        return Goal(goal_id=goal_id, goal_revision=1, source_utterance_id="turn", approved_slots=slots or {})

    def test_goal_step_picks_visible_or_below_fold_route_target(self):
        from server.agent.profiles import goal_step

        alarm = self.goal("CLOCK_ALARM", {"day": "tomorrow", "time": "07:00"})
        self.assertEqual(goal_step(alarm, screen("home", 1, ("launcher.clock", "launcher.gmail"))), "launcher.clock")
        hidden = screen("home", 1, ("launcher.gmail",),
                        evidence=("below_fold:home:launcher.clock", "scroll_down:home"))
        self.assertEqual(goal_step(alarm, hidden), "launcher.clock")
        self.assertIsNone(goal_step(alarm, screen("home", 1, ("launcher.gmail",))))
        font = self.goal("SET_FONT")
        self.assertEqual(goal_step(font, screen("settings_home", 2, ("settings.display",))), "settings.display")
        self.assertIsNone(goal_step(font, screen("settings_display", 3, ())))
