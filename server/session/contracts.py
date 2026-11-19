"""In-process version scopes and result envelopes shared by the session nodes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from math import isfinite
from time import monotonic_ns
from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable
from uuid import uuid4


Source = Literal["LIVE", "FIXTURE"]
T = TypeVar("T")
SCREEN_BOUNDARIES = {"protected": "PROTECTED", "unknown": "GAP"}


@runtime_checkable
class _WireFields(Protocol):
    """Allows an internal type to select the fields in its public contract."""

    def to_wire_fields(self) -> Mapping[str, object]: ...


def _wire_value(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    if isinstance(value, _WireFields):
        value = value.to_wire_fields()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Wire objects require string keys")
        return {key: _wire_value(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _wire_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_wire_value(item) for item in value]
    raise TypeError(f"Unsupported wire value of type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Basis:
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

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None:
                continue
            if item.name.endswith("_id") or item.name == "projection_epoch":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Invalid {item.name}")
            elif type(value) is not int or value < 0:
                raise ValueError(f"Invalid {item.name}")


@dataclass(frozen=True, slots=True)
class ResultRef:
    trace_id: str
    node_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class StepDiagnostic:
    status: Literal["success", "failed", "skipped"]
    code: str | None


@dataclass(frozen=True, slots=True)
class ErrorDescriptor:
    code: str
    step: str
    retryable: bool


@dataclass(frozen=True, slots=True)
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

    @property
    def ref(self) -> ResultRef:
        return ResultRef(self.trace_id, self.node_id, self.run_id)


@dataclass(frozen=True, slots=True)
class NodeResult(Generic[T]):
    status: Literal["success", "partial", "error"]
    outcome: str | None
    data: T | None
    diagnostics: Diagnostic

    def to_dict(self) -> dict:
        """Return the common JSON envelope, retaining explicit nulls and result provenance."""
        return _wire_value(self)


class Run:
    """A single node invocation. Only opaque result references enter diagnostics."""

    def __init__(self, basis: Basis, *, source: Source, trace_id: str | None = None,
                 input_refs: tuple[ResultRef, ...] = ()) -> None:
        if not isinstance(basis, Basis) or source not in ("LIVE", "FIXTURE"):
            raise ValueError("Invalid run context")
        if any(not isinstance(ref, ResultRef) for ref in input_refs):
            raise ValueError("Invalid result reference")
        self.basis = basis
        self.source = source
        self.trace_id = trace_id or uuid4().hex
        self.run_id = uuid4().hex
        self.input_refs = tuple(input_refs)
        self._started = monotonic_ns()

    def _diagnostic(self, node_id: str, step: str, error: ErrorDescriptor | None) -> Diagnostic:
        checkpoint = StepDiagnostic("failed", error.code) if error else StepDiagnostic("success", None)
        return Diagnostic(
            self.trace_id, self.run_id, node_id, self.source, self.basis, self.input_refs,
            (monotonic_ns() - self._started) // 1_000_000, {step: checkpoint}, error,
        )

    def success(self, node_id: str, outcome: str, data: T, step: str = "apply") -> NodeResult[T]:
        return NodeResult("success", outcome, data, self._diagnostic(node_id, step, None))

    def error(self, node_id: str, code: str, step: str = "validate") -> NodeResult:
        error = ErrorDescriptor(code, step, False)
        return NodeResult("error", None, None, self._diagnostic(node_id, step, error))
