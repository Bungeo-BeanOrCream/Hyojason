"""공통 NodeResult·Diagnostic 봉투 (docs/node-result-contract.md v1)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar

from .enums import Source
from .types import HelpSnapshot

T = TypeVar("T")


class Status(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


class StepStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    STALE_BASIS = "STALE_BASIS"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    NO_CONSENT = "NO_CONSENT"
    CAPTURE_GAP = "CAPTURE_GAP"
    PROTECTED_SCREEN = "PROTECTED_SCREEN"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NO_CANDIDATES = "NO_CANDIDATES"
    SEARCH_INCOMPLETE = "SEARCH_INCOMPLETE"
    OVERLAY_BLOCKED = "OVERLAY_BLOCKED"
    TOUCH_BLOCKED = "TOUCH_BLOCKED"
    DUPLICATE = "DUPLICATE"
    ALREADY_ENDED = "ALREADY_ENDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class Basis:
    """실행 시작에 잠근 적용 조건. 생성 전이거나 관여하지 않은 버전은 None."""

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

    @classmethod
    def from_snapshot(cls, snapshot: HelpSnapshot) -> Basis:
        return cls(
            pair_id=snapshot.pair_id,
            call_id=snapshot.call_id,
            help_id=snapshot.help_id,
            event_seq=snapshot.event_seq,
            consent_revision=snapshot.consent_revision,
            goal_revision=snapshot.goal.goal_revision if snapshot.goal else None,
            mode_revision=snapshot.mode_revision,
            instruction_revision=(
                snapshot.instruction.instruction_revision if snapshot.instruction else None
            ),
            projection_epoch=snapshot.screen.projection_epoch if snapshot.screen else None,
            screen_revision=snapshot.screen.screen_revision if snapshot.screen else None,
            utterance_seq=None,
        )


@dataclass(frozen=True)
class ResultRef:
    trace_id: str
    node_id: str
    run_id: str


@dataclass(frozen=True)
class StepDiagnostic:
    status: StepStatus
    code: str | None = None
    duration_ms: int | None = None
    count: int | None = None
    retryable: bool | None = None

    # 직렬화 시 None이면 키를 생략하는 선택 필드
    __omit_none__ = ("duration_ms", "count", "retryable")


@dataclass(frozen=True)
class ErrorDescriptor:
    code: str
    step: str
    retryable: bool


@dataclass(frozen=True)
class Diagnostic:
    trace_id: str
    run_id: str
    node_id: str
    source: Source
    basis: Basis
    input_refs: tuple[ResultRef, ...]
    duration_ms: int
    steps: Mapping[str, StepDiagnostic]
    terminal_error: ErrorDescriptor | None


@dataclass(frozen=True)
class NodeResult(Generic[T]):
    status: Status
    outcome: str | None
    data: T | None
    diagnostics: Diagnostic

    @property
    def ref(self) -> ResultRef:
        d = self.diagnostics
        return ResultRef(trace_id=d.trace_id, node_id=d.node_id, run_id=d.run_id)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass
class NodeRun:
    """한 번의 노드 호출 기록기. 단계 진단을 쌓고 계약에 맞는 NodeResult를 만든다."""

    node_id: str
    trace_id: str
    source: Source
    clock_ns: Callable[[], int] = time.monotonic_ns
    run_id: str = field(default_factory=lambda: new_id("run"))
    steps: dict[str, StepDiagnostic] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._started_ns = self.clock_ns()

    def step(
        self,
        name: str,
        status: StepStatus,
        code: str | None = None,
        *,
        count: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        if status is not StepStatus.SUCCESS and code is None:
            raise ValueError(f"step '{name}': failed/skipped 단계에는 code가 필요하다")
        self.steps[name] = StepDiagnostic(
            status=status, code=code, count=count, retryable=retryable
        )

    def _diagnostic(
        self,
        basis: Basis,
        input_refs: Iterable[ResultRef],
        terminal_error: ErrorDescriptor | None,
    ) -> Diagnostic:
        elapsed_ms = max(0, (self.clock_ns() - self._started_ns) // 1_000_000)
        return Diagnostic(
            trace_id=self.trace_id,
            run_id=self.run_id,
            node_id=self.node_id,
            source=self.source,
            basis=basis,
            input_refs=tuple(input_refs),
            duration_ms=int(elapsed_ms),
            steps=dict(self.steps),
            terminal_error=terminal_error,
        )

    def success(
        self,
        outcome: str,
        data: T,
        basis: Basis,
        input_refs: Iterable[ResultRef] = (),
    ) -> NodeResult[T]:
        return NodeResult(
            status=Status.SUCCESS,
            outcome=outcome,
            data=data,
            diagnostics=self._diagnostic(basis, input_refs, None),
        )

    def partial(
        self,
        outcome: str,
        data: T,
        basis: Basis,
        input_refs: Iterable[ResultRef] = (),
    ) -> NodeResult[T]:
        """일부 경로만 실패. 실패한 단계는 steps에만 남기고 terminal_error는 비운다."""
        return NodeResult(
            status=Status.PARTIAL,
            outcome=outcome,
            data=data,
            diagnostics=self._diagnostic(basis, input_refs, None),
        )

    def error(
        self,
        code: ErrorCode,
        step: str,
        basis: Basis,
        *,
        retryable: bool = False,
        input_refs: Iterable[ResultRef] = (),
    ) -> NodeResult[T]:
        self.step(step, StepStatus.FAILED, code.value, retryable=retryable)
        return NodeResult(
            status=Status.ERROR,
            outcome=None,
            data=None,
            diagnostics=self._diagnostic(
                basis,
                input_refs,
                ErrorDescriptor(code=code.value, step=step, retryable=retryable),
            ),
        )
