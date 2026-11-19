"""효자손 서버 노드가 공유하는 입력과 결과 데이터 타입."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Literal, TypeAlias, TypeVar

Mode = Literal["CALL_CHILD_LED", "POST_CALL_AGENT"]
Source = Literal["LIVE", "FIXTURE"]
SpeakerRole = Literal["parent", "child", "PARENT", "CHILD"]
NodeStatus = Literal["success", "partial", "error"]
StepStatus = Literal["success", "failed", "skipped"]
Action = Literal["TAP", "BACK", "SCROLL_DOWN", "SCROLL_UP", "INPUT_HINT", "WAIT"]
GoalId = Literal[
    "SET_FONT", "SET_TIMEOUT", "CLOCK_ALARM", "MAP_ROUTE", "SMS_SEND", "MAIL_OPEN"
]
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
OutputT = TypeVar("OutputT", covariant=True)


@dataclass(frozen=True, kw_only=True)
class Basis:
    """실행 당시 스코프와 버전. 해당하지 않는 필드는 None이다."""

    pair_id: str | None = None
    call_id: str | None = None
    help_id: str | None = None
    event_seq: int | None = None
    consent_revision: int | None = None
    goal_revision: int | None = None
    mode_revision: int | None = None
    instruction_revision: int | None = None
    projection_epoch: str | None = None
    screen_revision: int | None = None
    utterance_seq: int | None = None


@dataclass(frozen=True, kw_only=True)
class ResultRef:
    trace_id: str
    node_id: str
    run_id: str


@dataclass(frozen=True, kw_only=True)
class StepDiagnostic:
    __omit_none__ = ("duration_ms", "count", "retryable")

    status: StepStatus
    code: str | None
    duration_ms: int | None = None
    count: int | None = None
    retryable: bool | None = None


@dataclass(frozen=True, kw_only=True)
class ErrorDescriptor:
    code: str
    step: str
    retryable: bool


@dataclass(frozen=True, kw_only=True)
class Diagnostic:
    trace_id: str
    run_id: str
    node_id: str
    source: Source
    basis: Basis
    input_refs: tuple[ResultRef, ...]
    duration_ms: int
    steps: dict[str, StepDiagnostic]
    terminal_error: ErrorDescriptor | None


@dataclass(frozen=True, kw_only=True)
class NodeResult(Generic[OutputT]):
    """success/partial은 outcome과 data를, error는 terminal_error를 담는다."""

    status: NodeStatus
    outcome: str | None
    data: OutputT | None
    diagnostics: Diagnostic

    @property
    def ref(self) -> ResultRef:
        return ResultRef(
            trace_id=self.diagnostics.trace_id,
            node_id=self.diagnostics.node_id,
            run_id=self.diagnostics.run_id,
        )


@dataclass(frozen=True, kw_only=True)
class FinalUtterance:
    utterance_id: str
    utterance_seq: int
    speaker_role: SpeakerRole
    text: str = field(repr=False)
    call_id: str
    source: Source


@dataclass(frozen=True, kw_only=True)
class InstructionDraft:
    source_utterance_id: str
    target_key: str | None
    action: Action
    supersedes_previous: bool


@dataclass(frozen=True, kw_only=True)
class Instruction:
    instruction_id: str
    instruction_revision: int
    source_utterance_id: str
    target_key: str | None
    action: Action
    origin_screen_revision: int | None


@dataclass(frozen=True, kw_only=True)
class GoalDraft:
    goal_id: GoalId
    approved_slots: dict[str, JsonValue] = field(repr=False)
    source_utterance_id: str


@dataclass(frozen=True, kw_only=True)
class GoalBelief:
    """Zoro-1 GOAL의 최신 확률 분포. 지원 목표 전체와 NONE을 담고 발화 원문은 담지 않는다."""

    probabilities: dict[str, float]
    source_utterance_id: str
    utterance_seq: int
    model_revision: str


@dataclass(frozen=True, kw_only=True)
class Goal:
    goal_id: GoalId
    goal_revision: int
    approved_slots: dict[str, JsonValue] = field(repr=False)
    source_utterance_id: str


@dataclass(frozen=True, kw_only=True)
class ScreenState:
    """부모 화면의 서버용 요약. 대상 좌표와 원본 프레임은 단말에 둔다."""

    projection_epoch: str
    screen_revision: int
    screen_kind: str
    target_ids: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    observation_health: str


@dataclass(frozen=True, kw_only=True)
class TrailEntry:
    from_screen: ScreenState
    to_screen: ScreenState
    projection_epoch: str
    evidence_codes: tuple[str, ...]
    reversible: bool
    observed_seq: int


@dataclass(frozen=True, kw_only=True)
class InstructionTrace:
    instruction_id: str
    instruction_revision: int
    target_id: str | None
    origin_screen_revision: int | None
    evidence_codes: tuple[str, ...]
    outcome: str


@dataclass(frozen=True, kw_only=True)
class NavigationTrail:
    entries: tuple[TrailEntry, ...]
    directives: tuple[InstructionTrace, ...]
    gap_after_seq: int | None
    history_truncated: bool = False


@dataclass(frozen=True, kw_only=True)
class Candidate:
    choice_id: str
    action: Action
    target_id: str | None
    label_key: str
    evidence_codes: tuple[str, ...]
    path_distance: int | None


@dataclass(frozen=True, kw_only=True)
class CandidateSet:
    candidates: tuple[Candidate, ...]
    coverage: Literal["complete", "partial", "policy_blocked"]
    active_count: int
    recovery_count: int


@dataclass(frozen=True, kw_only=True)
class TargetDecision:
    location_kind: Literal["visible", "below_fold", "unknown", "blocked"]
    target_id: str | None
    anchor_screen_revision: int | None
    evidence_codes: tuple[str, ...]
    model_revision: str | None


@dataclass(frozen=True, kw_only=True)
class GuideProposal:
    decision_id: str
    mode: Mode
    action: Action
    target_id: str | None
    message_key: str
    confidence: float
    valid_for_ms: int
    basis: Basis


@dataclass(frozen=True, kw_only=True)
class RecoveryHint:
    instruction_revision: int
    anchor_screen_revision: int
    first_edge_ref: str
    path_distance: int
    basis: Basis


@dataclass(frozen=True, kw_only=True)
class ArrivalDecision:
    arrival_id: str
    arrived: bool
    goal_revision: int
    evidence_codes: tuple[str, ...]
    basis: Basis
