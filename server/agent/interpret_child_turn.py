"""N-15. 자녀의 확정 발화에서 현재 지시와 통화 후 이어갈 목표를 분리한다.

입력은 최신 자녀 발화, 기존 목표, 목표 카탈로그 버전, Basis다.
출력은 버튼 지시 초안, 목표 초안, 선행 지시 교체 여부다.
유효 지시는 먼저 전달하고, 명시적인 목표가 있을 때 N-14로 별도 판단한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Literal

from ..contracts import (
    Basis,
    FinalUtterance,
    Goal,
    GoalDraft,
    GoalId,
    InstructionDraft,
    JsonValue,
    Mode,
    NodeResult,
)
from ._execution import ExecutionContext, NodeRun
from .infer_zoro_choice import (
    Choice,
    InferZoroChoiceInput,
    ZoroChoiceRunner,
    approved_selection,
)
from .profiles import GOAL_LABELS, POLICY_REVISION, TARGETS, is_dangerous, normalize

TurnKind = Literal["instruction", "goal", "instruction_and_goal", "none", "blocked"]
_TAP_WORDS = re.compile(r"누르|눌러|골라|선택|터치|클릭|들어가|열어")
_CLOCK_TIME = re.compile(
    r"(오전|오후|아침|저녁)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?"
)
_CLAUSES = re.compile(r"[.!?;\n,]+|그리고|그다음|그 다음")


@dataclass(frozen=True, kw_only=True)
class InterpretChildTurnInput:
    mode: Mode
    speech: FinalUtterance
    existing_goal: Goal | None
    goal_catalog_revision: int
    basis: Basis
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class InterpretChildTurnOutput:
    instruction_draft: InstructionDraft | None
    goal_draft: GoalDraft | None
    turn_kind: TurnKind
    confidence: float | None
    supersedes_previous: bool


@dataclass(kw_only=True)
class InterpretChildTurn:
    infer_zoro_choice: ZoroChoiceRunner
    node_id = "N-15"

    def run(
        self,
        request: InterpretChildTurnInput,
        *,
        on_instruction: Callable[[InstructionDraft], None] | None = None,
    ) -> NodeResult[InterpretChildTurnOutput]:
        """지시 초안을 먼저 돌려주고 명시 목표는 별도로 판단한다."""
        speech = request.speech
        run = NodeRun(self.node_id, request.basis, request.execution)
        if speech.source == "FIXTURE":
            run.source = "FIXTURE"
        if request.mode != "CALL_CHILD_LED" or speech.speaker_role.lower() != "child":
            return run.fail("NOT_APPLICABLE", "validate")
        if (
            speech.call_id != request.basis.call_id
            or speech.utterance_seq != request.basis.utterance_seq
        ):
            return run.fail("STALE_BASIS", "validate")
        if request.goal_catalog_revision != POLICY_REVISION:
            return run.fail("STALE_BASIS", "goal_catalog")
        run.ok("validate")

        if is_dangerous(speech.text):
            return run.finish(
                "policy_denied",
                self._output(None, None, None, turn_kind="blocked", supersedes=True),
            )

        draft = parse_instruction(speech)
        if draft is not None and on_instruction is not None:
            on_instruction(draft)
        run.ok("instruction", count=1 if draft else 0)
        proposals, incomplete = goal_options(speech.text)
        if not proposals:
            run.skip("goal", "MISSING_GOAL_SLOT" if incomplete else "NOT_APPLICABLE")
            if draft and incomplete:
                return run.finish(
                    "instruction_only",
                    self._output(draft, None, None),
                    status="partial",
                )
            return run.finish(
                "instruction_ready" if draft else "waiting",
                self._output(draft, None, None),
            )

        choices = tuple(
            Choice(choice_id=goal_id, description=GOAL_LABELS[goal_id])
            for goal_id in proposals
        )
        model = self.infer_zoro_choice(
            InferZoroChoiceInput(
                mode=request.mode,
                question_kind="GOAL",
                choices=(
                    *choices,
                    Choice(choice_id="NONE", description="명시적인 지원 목표 없음"),
                ),
                context={
                    "recognized_goal_options": {
                        key: value for key, value in proposals.items()
                    }
                },
                basis=request.basis,
                deadline_ms=1000,
                execution=run.for_child(),
            )
        )
        run.child(model, step="goal")
        if model.data is None:
            if draft:
                return run.finish(
                    "instruction_only",
                    self._output(draft, None, None),
                    status="partial",
                )
            return run.propagate(model, "goal")
        winner = approved_selection(model.data, set(proposals))
        goal = next(
            (
                GoalDraft(
                    goal_id=goal_id,
                    approved_slots=slots,
                    source_utterance_id=speech.utterance_id,
                )
                for goal_id, slots in proposals.items()
                if goal_id == winner
            ),
            None,
        )
        confidence = model.data.probabilities.get(model.data.selected_id)
        outcome = "instruction_ready" if draft else ("goal_only" if goal else "waiting")
        return run.finish(outcome, self._output(draft, goal, confidence))

    @staticmethod
    def _output(
        draft: InstructionDraft | None,
        goal: GoalDraft | None,
        confidence: float | None,
        *,
        turn_kind: TurnKind | None = None,
        supersedes: bool = False,
    ) -> InterpretChildTurnOutput:
        if turn_kind is None:
            if draft and goal:
                turn_kind = "instruction_and_goal"
            elif draft:
                turn_kind = "instruction"
            else:
                turn_kind = "goal" if goal else "none"
        return InterpretChildTurnOutput(
            instruction_draft=draft,
            goal_draft=goal,
            turn_kind=turn_kind,
            confidence=confidence,
            supersedes_previous=supersedes or draft is not None,
        )


def parse_instruction(speech: FinalUtterance) -> InstructionDraft | None:
    for clause in _CLAUSES.split(speech.text):
        clause = re.split(r"말고|대신", clause)[-1]
        if not _TAP_WORDS.search(clause):
            continue
        normalized = normalize(clause)
        if any(
            stop in normalized
            for stop in ("누르지마", "누르면안", "누르지말", "선택하지마")
        ):
            continue
        lengths = {
            target_id: max(
                (
                    len(normalize(alias))
                    for alias in (profile.label, *profile.aliases)
                    if normalize(alias) in normalized
                ),
                default=0,
            )
            for target_id, profile in TARGETS.items()
            if profile.action == "TAP"
        }
        longest = max(lengths.values(), default=0)
        matches = [
            target_id
            for target_id, size in lengths.items()
            if size == longest and size > 0
        ]
        if matches or any(
            word in normalized for word in ("버튼", "그거", "저거", "그곳")
        ):
            return InstructionDraft(
                source_utterance_id=speech.utterance_id,
                target_key=matches[0] if len(matches) == 1 else None,
                action="TAP",
                supersedes_previous=True,
            )
    return None


def goal_options(text: str) -> tuple[dict[GoalId, dict[str, JsonValue]], bool]:
    normalized = normalize(text)
    eligible: dict[GoalId, dict[str, JsonValue]] = {}
    incomplete = False
    if any(word in normalized for word in ("글씨", "글자", "폰트")) and any(
        word in normalized for word in ("크게", "키우", "키워", "너무작", "작아")
    ):
        eligible["SET_FONT"] = {"delta": 1}
    if ("화면" in normalized or "자동꺼짐" in normalized) and any(
        word in normalized for word in ("늘려", "늘리", "바꿔", "설정해")
    ):
        if re.search(r"(?:^|\D)10\s*분|십\s*분", text):
            eligible["SET_TIMEOUT"] = {"duration_minutes": 10}
        else:
            incomplete = True
    if "알람" in normalized and any(
        word in normalized for word in ("맞추", "맞춰", "설정", "울리")
    ):
        match = _CLOCK_TIME.search(text)
        if match:
            period, raw_hour, raw_minute = match.groups()
            hour, minute = int(raw_hour), int(raw_minute or 0)
            valid_clock = 1 <= hour <= 12 if period else (hour == 0 or 13 <= hour < 24)
            if valid_clock and period:
                hour = hour % 12 + (12 if period in {"오후", "저녁"} else 0)
            if valid_clock and 0 <= minute < 60:
                if "내일" in normalized or "오늘" in normalized:
                    eligible["CLOCK_ALARM"] = {
                        "day": "tomorrow" if "내일" in normalized else "today",
                        "time": f"{hour:02d}:{minute:02d}",
                    }
        incomplete |= "CLOCK_ALARM" not in eligible
    if "길찾기" in normalized and "시연장소" in normalized:
        eligible["MAP_ROUTE"] = {"destination_id": "demo_place"}
    if ("문자" in normalized or "메시지" in normalized) and "보내" in normalized:
        if "시연상대" in normalized and "시연문구" in normalized:
            eligible["SMS_SEND"] = {
                "recipient_id": "demo_recipient",
                "message_id": "demo_message",
            }
    if (
        any(word in normalized for word in ("시연메일", "테스트메일"))
        and "열어" in normalized
    ):
        eligible["MAIL_OPEN"] = {"mail_id": "demo_mail"}
    return eligible, incomplete
