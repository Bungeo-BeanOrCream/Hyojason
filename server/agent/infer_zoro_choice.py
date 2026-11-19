"""N-14. 승인된 선택지를 Zoro-1에 전달하는 공통 진입점.

입력은 질문 종류, 선택지, 정제한 맥락, Basis, 호출 제한 시간이다.
출력은 선택 ID, 확률, 모델 버전, 지연, 실행 출처를 담은 NodeResult다.
모드와 선택지 소속을 검사하고 타임아웃·잘못된 응답을 오류로 반환한다.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isfinite
from os import environ
from threading import Lock
from time import monotonic_ns
from typing import Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

from ..contracts import Basis, ErrorDescriptor, JsonValue, Mode, NodeResult, Source
from ._execution import ExecutionContext, NodeRun

QuestionKind = Literal[
    "GOAL", "TARGET_LOCATION", "ACTION_MATCH", "NEXT_ACTION", "ARRIVAL"
]
QuestionTransport = Callable[[dict[str, JsonValue], float], object]

_QUESTIONS: dict[QuestionKind, tuple[Mode, str]] = {
    "GOAL": ("CALL_CHILD_LED", "자녀가 말한 명시적 지원 목표를 하나 고르세요."),
    "TARGET_LOCATION": (
        "CALL_CHILD_LED",
        "현재 지시가 가리키는 검증된 버튼 또는 스크롤 후보를 고르세요. 모호하면 UNKNOWN입니다.",
    ),
    "ACTION_MATCH": (
        "CALL_CHILD_LED",
        "검증된 화면 전이로 지시와 실제 조작의 일치 여부를 고르세요. 근거가 불충분하면 UNKNOWN입니다.",
    ),
    "NEXT_ACTION": (
        "POST_CALL_AGENT",
        "확정 목표로 가는 현재 화면의 유효한 다음 행동 하나를 고르세요.",
    ),
    "ARRIVAL": (
        "POST_CALL_AGENT",
        "현재 관찰에 나타난 목표별 필수 증거로 도착 여부를 판단하세요.",
    ),
}


@dataclass(frozen=True, kw_only=True)
class Choice:
    choice_id: str
    description: str


@dataclass(frozen=True, kw_only=True)
class InferZoroChoiceInput:
    mode: Mode
    question_kind: QuestionKind
    choices: tuple[Choice, ...]
    context: dict[str, JsonValue] = field(repr=False)
    basis: Basis
    deadline_ms: int
    execution: ExecutionContext | None = None


@dataclass(frozen=True, kw_only=True)
class InferZoroChoiceOutput:
    selected_id: str
    probabilities: dict[str, float]
    model_revision: str
    latency_ms: int
    inference_source: Source


ZoroChoiceRunner = Callable[[InferZoroChoiceInput], NodeResult[InferZoroChoiceOutput]]
_ModelResult = InferZoroChoiceOutput | ErrorDescriptor


class InferZoroChoice:
    node_id = "N-14"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        model_revision: str | None = None,
        bearer_token: str | None = None,
        transport: QuestionTransport | None = None,
        transport_source: Source = "FIXTURE",
    ) -> None:
        self.endpoint = endpoint
        self.model_revision = model_revision
        self.bearer_token = bearer_token
        self._transport = transport or self._post
        self._transport_source: Source = transport_source if transport else "LIVE"
        self._lock = Lock()
        self._inflight: dict[str, Future[_ModelResult]] = {}
        self._cache: OrderedDict[str, _ModelResult] = OrderedDict()

    @classmethod
    def from_environment(cls) -> InferZoroChoice:
        return cls(
            endpoint=environ.get("ZORO_DECIDE_URL"),
            model_revision=environ.get("ZORO_MODEL_REVISION"),
            bearer_token=environ.get("ZORO_DECIDE_TOKEN"),
        )

    def run(self, request: InferZoroChoiceInput) -> NodeResult[InferZoroChoiceOutput]:
        run = NodeRun(self.node_id, request.basis, request.execution)
        question = _QUESTIONS.get(request.question_kind)
        if not question or request.mode != question[0] or request.deadline_ms <= 0:
            return run.fail("INVALID_INPUT", "validate")
        criteria: dict[str, JsonValue] = {
            choice.choice_id: choice.description for choice in request.choices
        }
        if (
            len(criteria) < 2
            or len(criteria) != len(request.choices)
            or any(
                not choice.choice_id.strip() or not choice.description.strip()
                for choice in request.choices
            )
        ):
            return run.fail("INVALID_INPUT", "validate")
        if not self.model_revision:
            return run.fail("DEPENDENCY_UNAVAILABLE", "model")
        if self._transport_source == "FIXTURE":
            run.source = "FIXTURE"
        payload: dict[str, JsonValue] = {
            "request": {
                "state": request.context,
                "questions": {
                    "decision": {
                        "type": "choice",
                        "instructions": question[1],
                        "criteria": criteria,
                    }
                },
            },
            "reasoning": False,
        }
        try:
            key = self._cache_key(request, payload)
        except (TypeError, ValueError):
            return run.fail("INVALID_INPUT", "validate")
        run.ok("validate")
        result, reused = self._decide_once(key, request, payload, self.model_revision)
        if isinstance(result, ErrorDescriptor):
            return run.fail(result.code, result.step, retryable=result.retryable)
        if reused:
            run.skip("model", "DUPLICATE")
        else:
            run.ok("model")
        # Return a private copy so a caller cannot modify the cache.
        output = InferZoroChoiceOutput(
            selected_id=result.selected_id,
            probabilities=result.probabilities.copy(),
            model_revision=result.model_revision,
            latency_ms=result.latency_ms,
            inference_source=result.inference_source,
        )
        return run.finish("inferred", output)

    @staticmethod
    def _cache_key(request: InferZoroChoiceInput, payload: dict[str, JsonValue]) -> str:
        b = request.basis
        versions = (
            b.pair_id,
            b.call_id,
            b.help_id,
            b.consent_revision,
            b.mode_revision,
            b.goal_revision,
            b.instruction_revision,
            b.projection_epoch,
            b.screen_revision,
            b.utterance_seq,
            request.mode,
            request.question_kind,
        )
        material = json.dumps((versions, payload), sort_keys=True, allow_nan=False)
        return sha256(material.encode()).hexdigest()

    def _decide_once(
        self,
        key: str,
        request: InferZoroChoiceInput,
        payload: dict[str, JsonValue],
        revision: str,
    ) -> tuple[_ModelResult, bool]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key], True
            pending = self._inflight.get(key)
            owner = pending is None
            if pending is None:
                pending = self._inflight[key] = Future()
        if not owner:
            try:
                return pending.result(timeout=request.deadline_ms / 1000), True
            except TimeoutError:
                return ErrorDescriptor(
                    code="TIMEOUT", step="model", retryable=True
                ), True

        try:
            output = self._request_model(request, payload, revision)
        except BaseException as exc:
            # Release coalesced callers even when interrupted by the host.
            fallback = ErrorDescriptor(
                code="DEPENDENCY_UNAVAILABLE", step="model", retryable=True
            )
            with self._lock:
                del self._inflight[key]
                pending.set_result(fallback)
            if not isinstance(exc, Exception):
                raise
            return fallback, False
        with self._lock:
            self._cache[key] = output
            if len(self._cache) > 128:
                self._cache.popitem(last=False)
            del self._inflight[key]
            pending.set_result(output)
        return output, False

    def _request_model(
        self,
        request: InferZoroChoiceInput,
        payload: dict[str, JsonValue],
        revision: str,
    ) -> _ModelResult:
        started = monotonic_ns()
        try:
            raw = self._transport(payload, request.deadline_ms / 1000)
        except TimeoutError:
            return ErrorDescriptor(code="TIMEOUT", step="model", retryable=True)
        except URLError as error:
            code = (
                "TIMEOUT"
                if isinstance(error.reason, TimeoutError)
                else "DEPENDENCY_UNAVAILABLE"
            )
            return ErrorDescriptor(code=code, step="model", retryable=True)
        except OSError:
            return ErrorDescriptor(
                code="DEPENDENCY_UNAVAILABLE", step="model", retryable=True
            )
        except (TypeError, ValueError):
            return ErrorDescriptor(
                code="INVALID_MODEL_RESPONSE", step="model", retryable=False
            )
        latency_ms = (monotonic_ns() - started) // 1_000_000
        if latency_ms >= request.deadline_ms:
            return ErrorDescriptor(code="TIMEOUT", step="model", retryable=True)

        approved = {choice.choice_id for choice in request.choices}
        try:
            if not isinstance(raw, dict):
                raise ValueError
            if raw.get("model_revision", revision) != revision:
                raise ValueError
            decision = raw["answers"]["decision"]
            if decision["type"] != "choice":
                raise ValueError
            selected = decision["choice"]
            probabilities = decision["probabilities"]
            if (
                not isinstance(selected, str)
                or selected not in approved
                or not isinstance(probabilities, dict)
                or probabilities.keys() != approved
            ):
                raise ValueError
            scores: dict[str, float] = {}
            for choice_id in approved:
                score = probabilities[choice_id]
                if (
                    type(score) not in (int, float)
                    or not isfinite(score)
                    or not 0 <= score <= 1
                ):
                    raise ValueError
                scores[choice_id] = float(score)
            if scores[selected] != max(scores.values()):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            return ErrorDescriptor(
                code="INVALID_MODEL_RESPONSE", step="response", retryable=False
            )
        return InferZoroChoiceOutput(
            selected_id=selected,
            probabilities=scores,
            model_revision=revision,
            latency_ms=latency_ms,
            inference_source=self._transport_source,
        )

    def _post(self, payload: dict[str, JsonValue], timeout: float) -> object:
        if not self.endpoint:
            raise OSError("ZORO_DECIDE_URL is not configured")
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)


def approved_selection(
    output: InferZoroChoiceOutput,
    allowed_ids: set[str],
    *,
    minimum_probability: float = 0.70,
    minimum_margin: float = 0.15,
) -> str | None:
    selected = output.selected_id
    if selected not in allowed_ids:
        return None
    score = output.probabilities.get(selected, 0.0)
    alternative = max(
        (
            value
            for choice_id, value in output.probabilities.items()
            if choice_id != selected
        ),
        default=0.0,
    )
    if not isfinite(score) or not isfinite(alternative):
        return None
    if score < minimum_probability or score - alternative < minimum_margin:
        return None
    return selected
