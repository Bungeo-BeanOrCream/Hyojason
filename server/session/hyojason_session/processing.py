"""Invocation context for the session writer and navigation recorder."""

from dataclasses import fields

from .common.enums import Source
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, ResultRef, StepStatus, new_id


SCREEN_BOUNDARIES = {"protected": "PROTECTED", "unknown": "GAP"}


class Run:
    def __init__(self, basis: Basis, *, source: Source | str, trace_id: str | None = None,
                 input_refs: tuple[ResultRef, ...] = ()):
        if not isinstance(basis, Basis):
            raise ValueError("Invalid run basis")
        for field in fields(basis):
            value = getattr(basis, field.name)
            if value is None:
                continue
            if field.name.endswith("_id") or field.name == "projection_epoch":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Invalid {field.name}")
            elif type(value) is not int or value < 0:
                raise ValueError(f"Invalid {field.name}")
        self.basis = basis
        self.source = Source(source)
        self.trace_id = trace_id or new_id("trace")
        self.input_refs = tuple(input_refs)
        self._run = NodeRun("", self.trace_id, self.source)

    @property
    def run_id(self) -> str:
        return self._run.run_id

    def success(self, node_id, outcome, data, step="apply") -> NodeResult:
        self._run.node_id = node_id
        self._run.step(step, StepStatus.SUCCESS)
        return self._run.success(outcome, data, self.basis, self.input_refs)

    def error(self, node_id, code, step="validate") -> NodeResult:
        self._run.node_id = node_id
        return self._run.error(ErrorCode(code), step, self.basis, input_refs=self.input_refs)
