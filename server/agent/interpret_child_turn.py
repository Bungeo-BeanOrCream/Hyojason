"""N-15. 확정 발화에서 현재 지시를 분리하고, 대화 전체로 목표 확률을 갱신한다.

입력은 최신 확정 발화(두 역할), 현재 도움의 최근 확정 발화 창, 기존 목표와 직전 목표 확률,
목표 카탈로그 버전, Basis다. 출력은 버튼 지시 초안, 목표 확률, 목표 초안, 선행 지시 교체 여부다.
지시는 지시를 말하는 쪽(자녀) 발화에서만 규칙으로 해석해 먼저 전달한다.
목표는 키워드로 거르지 않는다. 발화마다 N-14가 지원 목표 전체와 NONE에 확률을 매기고,
1위가 채택 기준을 넘어 현재 목표와 달라지면 목표 초안을 낸다. 슬롯 값만 카탈로그 규칙으로 채운다.
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
    GoalBelief,
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
# STT는 "6시"와 "여섯 시"를 섞어 낸다(#22). 시는 숫자·한글 수사, 분은 숫자·한글 수사·"반"을 받는다.
_HOUR_WORDS = {
    "열한": 11, "열두": 12, "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
_MINUTE_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_CLOCK_TIME = re.compile(
    r"(오전|오후|아침|저녁|새벽|밤)?\s*(\d{1,2}|" + "|".join(_HOUR_WORDS) + r")\s*시"
    r"(?:\s*(반|\d{1,2}\s*분|[이삼사오]?십\s*[일이삼사오육칠팔구]?\s*분))?"
)
_PM_PERIODS = {"오후", "저녁", "밤"}
_CLAUSES = re.compile(r"[.!?;\n,]+|그리고|그다음|그 다음")
# 모델에 넘기는 최근 확정 발화 수 (docs/small-nodes/N-15.md, 초기값)
RECENT_UTTERANCE_LIMIT = 10
NO_GOAL = "NONE"


@dataclass(frozen=True, kw_only=True)
class InterpretChildTurnInput:
    mode: Mode
    speech: FinalUtterance
    existing_goal: Goal | None
    goal_catalog_revision: int
    basis: Basis
    recent_utterances: tuple[FinalUtterance, ...] = ()
    goal_belief: GoalBelief | None = None
    # 이번 도움의 지원 대상 가족 역할. speaker_role의 child/parent는 "지시하는 쪽/지원 대상"이라
    # 부모가 자녀를 돕는 도움에서는 실제 역할과 반대다. 모델에는 실제 역할을 알린다.
    assisted_role: Literal["PARENT", "CHILD"] = "PARENT"
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class InterpretChildTurnOutput:
    instruction_draft: InstructionDraft | None
    goal_belief: GoalBelief | None
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
        """지시 초안을 먼저 돌려주고, 목표 확률은 대화 전체로 별도 판단한다."""
        speech = request.speech
        run = NodeRun(self.node_id, request.basis, request.execution)
        if speech.source == "FIXTURE":
            run.source = "FIXTURE"
        if request.mode != "CALL_CHILD_LED":
            return run.fail("NOT_APPLICABLE", "validate")
        instructs = speech.speaker_role.lower() == "child"
        # 지시 범위의 utterance_seq는 지시하는 쪽 발화만 센다. 다른 쪽 발화의 목표 결과는
        # N-11이 최신 확정 발화 ID로 다시 확인한다.
        if speech.call_id != request.basis.call_id or (
            instructs and speech.utterance_seq != request.basis.utterance_seq
        ):
            return run.fail("STALE_BASIS", "validate")
        if request.goal_catalog_revision != POLICY_REVISION:
            return run.fail("STALE_BASIS", "goal_catalog")
        run.ok("validate")

        if instructs and is_dangerous(speech.text):
            return run.finish(
                "policy_denied",
                self._output(None, None, None, None, turn_kind="blocked", supersedes=True),
            )

        draft = parse_instruction(speech) if instructs else None
        if draft is not None and on_instruction is not None:
            on_instruction(draft)
        if instructs:
            run.ok("instruction", count=1 if draft else 0)
        else:
            run.skip("instruction", "NOT_APPLICABLE")

        conversation = recent_window(request.recent_utterances, speech)
        model = self.infer_zoro_choice(
            InferZoroChoiceInput(
                mode=request.mode,
                question_kind="GOAL",
                choices=(
                    *(
                        Choice(choice_id=goal_id, description=label)
                        for goal_id, label in GOAL_LABELS.items()
                    ),
                    Choice(choice_id=NO_GOAL, description="명시적인 지원 목표 없음"),
                ),
                context={
                    "conversation": [
                        speaker_context(item, request.assisted_role) for item in conversation
                    ],
                    "current_goal": (
                        request.existing_goal.goal_id if request.existing_goal else None
                    ),
                    "previous_probabilities": (
                        dict(request.goal_belief.probabilities)
                        if request.goal_belief
                        else None
                    ),
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
                    self._output(draft, None, None, None),
                    status="partial",
                )
            return run.propagate(model, "goal")
        belief = GoalBelief(
            probabilities=dict(model.data.probabilities),
            source_utterance_id=speech.utterance_id,
            utterance_seq=speech.utterance_seq,
            model_revision=model.data.model_revision,
        )
        # NONE 1위나 낮은 확신은 winner가 없다. 그때는 기존 목표를 지우지 않는다.
        winner = approved_selection(model.data, set(GOAL_LABELS))
        goal = None
        if winner is not None:
            slots = goal_slots(winner, [item.text for item in conversation])
            existing = request.existing_goal
            if slots is None:
                run.skip("goal_slots", "MISSING_GOAL_SLOT")
            elif existing is None or (existing.goal_id, existing.approved_slots) != (
                winner,
                slots,
            ):
                goal = GoalDraft(
                    goal_id=winner,
                    approved_slots=slots,
                    source_utterance_id=speech.utterance_id,
                )
        selected = winner or model.data.selected_id
        confidence = model.data.probabilities.get(selected)
        outcome = "instruction_ready" if draft else ("goal_only" if goal else "waiting")
        return run.finish(outcome, self._output(draft, belief, goal, confidence))

    @staticmethod
    def _output(
        draft: InstructionDraft | None,
        belief: GoalBelief | None,
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
            goal_belief=belief,
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


def speaker_context(item: FinalUtterance, assisted_role: str) -> dict[str, JsonValue]:
    """모델 입력용 화자: 실제 가족 역할(speaker)과 이번 도움에서의 관계(relation)."""
    helper = item.speaker_role.lower() == "child"
    assisted = assisted_role.lower()
    family = ("child" if assisted == "parent" else "parent") if helper else assisted
    return {
        "speaker": family,
        "relation": "helper" if helper else "assisted",
        "text": item.text,
    }


def recent_window(
    recent: tuple[FinalUtterance, ...], speech: FinalUtterance
) -> tuple[FinalUtterance, ...]:
    """현재 발화로 끝나는 최근 확정 발화 창. 텍스트는 이 호출에만 쓴다."""
    window = [item for item in recent if item.utterance_id != speech.utterance_id]
    window.append(speech)
    return tuple(window[-RECENT_UTTERANCE_LIMIT:])


# 시연 자산 ID는 D가 검증하는 demo/fixtures/goals.json 형식(demo-*)을 따른다.
# 모델이 고른 목표에 그대로 붙인다.
_FIXED_SLOTS: dict[GoalId, dict[str, JsonValue]] = {
    "SMS_SEND": {"recipient_id": "demo-recipient", "message_id": "demo-sms"},
    "MAIL_OPEN": {"mail_id": "demo-mail"},
}
# 길찾기 목적지: 대화에 나온 공개 장소 이름 → 시연 자산 ID (demo/fixtures/goals.json G07·G08)
_DEMO_PLACES: dict[str, str] = {
    "서울역": "demo-seoul-station",
    "국립중앙박물관": "demo-national-museum",
    "중앙박물관": "demo-national-museum",
}


def goal_slots(goal_id: str, texts: list[str]) -> dict[str, JsonValue] | None:
    """모델이 고른 목표의 필수 슬롯을 대화에서 채운다. 최신 발화를 먼저 본다. 없으면 None."""
    if goal_id in _FIXED_SLOTS:
        return dict(_FIXED_SLOTS[goal_id])  # type: ignore[index]
    newest_first = list(reversed(texts))
    if goal_id == "SET_FONT":
        # 단계는 말했을 때만 넣는다(goals.json G01 {"steps": 1}, G02 {}). D는 1–3단계를 허용한다.
        for text in newest_first:
            step = re.search(r"(한|두|세|[1-3])\s*단계", text)
            if step:
                return {"steps": {"한": 1, "두": 2, "세": 3}.get(step.group(1)) or int(step.group(1))}
        return {}
    if goal_id == "MAP_ROUTE":
        for text in newest_first:
            normalized = normalize(text)
            place = next((asset for name, asset in _DEMO_PLACES.items() if name in normalized), None)
            if place:
                return {"place": place}
        return None
    if goal_id == "SET_TIMEOUT":
        if any(re.search(r"(?:^|\D)10\s*분|십\s*분", text) for text in newest_first):
            return {"duration_minutes": 10}
        return None
    if goal_id == "CLOCK_ALARM":
        time = next(filter(None, map(_alarm_time, newest_first)), None)
        day = next(filter(None, map(_alarm_day, newest_first)), None)
        return {"day": day, "time": time} if time and day else None
    return None


def _alarm_time(text: str) -> str | None:
    for match in _CLOCK_TIME.finditer(text):
        period, raw_hour, raw_minute = match.groups()
        hour = int(raw_hour) if raw_hour.isdigit() else _HOUR_WORDS[raw_hour]
        minute = _minutes(raw_minute)
        # 오전/오후 없는 숫자 시각은 24시간 표기(0, 13–23시)만 받는다. 한글 수사는 오전·오후가 있어야 한다.
        valid_clock = 1 <= hour <= 12 if period else (raw_hour.isdigit() and (hour == 0 or 13 <= hour < 24))
        if valid_clock and period:
            hour = hour % 12 + (12 if period in _PM_PERIODS else 0)
        if valid_clock and minute is not None and 0 <= minute < 60:
            return f"{hour:02d}:{minute:02d}"
    return None


def _minutes(raw: str | None) -> int | None:
    if not raw:
        return 0
    raw = re.sub(r"\s|분", "", raw)
    if raw == "반":
        return 30
    if raw.isdigit():
        return int(raw)
    tens, _, ones = raw.partition("십")
    if tens and tens not in _MINUTE_DIGITS or ones and ones not in _MINUTE_DIGITS:
        return None
    return (_MINUTE_DIGITS[tens] if tens else 1) * 10 + (_MINUTE_DIGITS[ones] if ones else 0)


def _alarm_day(text: str) -> str | None:
    normalized = normalize(text)
    if "내일" in normalized:
        return "tomorrow"
    if "오늘" in normalized:
        return "today"
    return None
