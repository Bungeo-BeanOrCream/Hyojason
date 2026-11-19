"""판단 노드의 실행 결과와 공통 진단."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic_ns
from typing import Literal, Never, TypeVar
from uuid import uuid4

from ..contracts import (
    Basis,
    Diagnostic,
    ErrorDescriptor,
    NodeResult,
    ResultRef,
    Source,
    StepDiagnostic,
)

DataT = TypeVar("DataT")


@dataclass(frozen=True, kw_only=True)
class ExecutionContext:
    trace_id: str
    source: Source
    input_refs: tuple[ResultRef, ...] = ()


class NodeRun:
    def __init__(
        self,
        node_id: str,
        basis: Basis,
        execution: ExecutionContext | None,
    ) -> None:
        self.node_id = node_id
        self.basis = basis
        self.execution = execution or ExecutionContext(
            trace_id=uuid4().hex, source="FIXTURE"
        )
        self.run_id = uuid4().hex
        self.started = monotonic_ns()
        self.steps: dict[str, StepDiagnostic] = {}
        self.children: list[ResultRef] = []
        self.source = self.execution.source

    def ok(
        self, step: str, *, count: int | None = None, code: str | None = None
    ) -> None:
        self.steps[step] = StepDiagnostic(status="success", code=code, count=count)

    def skip(self, step: str, code: str = "NOT_APPLICABLE") -> None:
        self.steps[step] = StepDiagnostic(status="skipped", code=code)

    def child(self, result: NodeResult[object], *, step: str | None = None) -> None:
        diagnostic = result.diagnostics
        self.children.append(result.ref)
        if diagnostic.source == "FIXTURE":
            self.source = "FIXTURE"
        key = step or diagnostic.node_id
        if diagnostic.terminal_error:
            self.steps[key] = StepDiagnostic(
                status="failed",
                code=diagnostic.terminal_error.code,
                retryable=diagnostic.terminal_error.retryable,
            )
        else:
            self.ok(key, code=result.outcome)

    def for_child(self) -> ExecutionContext:
        return ExecutionContext(
            trace_id=self.execution.trace_id,
            source=self.source,
            input_refs=(*self.execution.input_refs, *self.children),
        )

    def finish(
        self,
        outcome: str,
        data: DataT,
        *,
        status: Literal["success", "partial"] = "success",
    ) -> NodeResult[DataT]:
        return NodeResult(
            status=status, outcome=outcome, data=data, diagnostics=self._diagnostic()
        )

    def fail(
        self, code: str, step: str, *, retryable: bool = False
    ) -> NodeResult[Never]:
        self.steps[step] = StepDiagnostic(
            status="failed", code=code, retryable=retryable
        )
        error = ErrorDescriptor(code=code, step=step, retryable=retryable)
        return NodeResult(
            status="error", outcome=None, data=None, diagnostics=self._diagnostic(error)
        )

    def propagate(self, result: NodeResult[object], step: str) -> NodeResult[Never]:
        error = result.diagnostics.terminal_error
        return self.fail(
            error.code if error else "DEPENDENCY_UNAVAILABLE",
            step,
            retryable=error.retryable if error else False,
        )

    def _diagnostic(self, error: ErrorDescriptor | None = None) -> Diagnostic:
        return Diagnostic(
            trace_id=self.execution.trace_id,
            run_id=self.run_id,
            node_id=self.node_id,
            source=self.source,
            basis=self.basis,
            input_refs=(*self.execution.input_refs, *self.children),
            duration_ms=(monotonic_ns() - self.started) // 1_000_000,
            steps=self.steps.copy(),
            terminal_error=error,
        )
