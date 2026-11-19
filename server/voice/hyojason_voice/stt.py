"""N-05 transcribe_korean_turn: STT 엔진이 낸 결과 하나를 계약 모양으로 바꾼다.

엔진(RTZR `sommers_ko`)은 API 키가 필요해 아직 붙이지 않았다.
엔진 어댑터는 SttEvent만 만들면 되고, 판정 규칙은 여기 한 곳에 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus

from .common import partial

MIN_CONFIDENCE = 0.6  # 이 미만 확정문은 transcript_uncertain.
# 주의: RTZR 스트리밍(livekit-plugins-rtzr 1.8)은 confidence를 주지 않아 지금은 이 판정이 걸리지 않는다.
# 잡음·겹침을 uncertain으로 거를 다른 기준(짧은 문장 등)은 실측 뒤 정한다.


class SttKind(StrEnum):
    PARTIAL = "partial"
    FINAL = "final"
    NO_SPEECH = "no_speech"
    ERROR = "error"


class Finality(StrEnum):
    PARTIAL = "PARTIAL"
    FINAL = "FINAL"


class Reliability(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"
    NONE = "NONE"


@dataclass(frozen=True)
class SttEvent:
    """엔진 어댑터 출력. text는 VolatileText라 repr·로그에 나오지 않게 한다."""

    kind: SttKind
    text: str | None = field(default=None, repr=False)
    confidence: float | None = None
    ended_at: float | None = None


@dataclass(frozen=True)
class TurnRequest:
    window_ref: str
    speaker_role: Role
    call_id: str
    turn_id: str
    stt_deadline_ms: int


@dataclass(frozen=True)
class Transcript:
    turn_id: str
    speaker_role: Role
    finality: Finality
    ended_at: float | None
    text: str | None = field(repr=False)
    reliability: Reliability


def transcribe_korean_turn(
    req: TurnRequest,
    event: SttEvent | None,
    *,
    trace_id: str,
    source: Source = Source.LIVE,
) -> NodeResult[Transcript]:
    """event=None은 stt_deadline_ms 안에 엔진 응답이 없었다는 뜻 → TIMEOUT (no_speech 아님)."""
    run = NodeRun("N-05", trace_id, source)
    basis = Basis(call_id=req.call_id)

    if event is None:
        return run.error(ErrorCode.TIMEOUT, "stt", basis, retryable=True)
    if event.kind is SttKind.ERROR:
        return run.error(ErrorCode.DEPENDENCY_UNAVAILABLE, "stt", basis, retryable=True)
    run.step("stt", StepStatus.SUCCESS)

    def out(fin: Finality, rel: Reliability, text: str | None) -> Transcript:
        return Transcript(req.turn_id, req.speaker_role, fin, event.ended_at, text, rel)

    if event.kind is SttKind.NO_SPEECH or (event.kind is SttKind.FINAL and not (event.text or "").strip()):
        return run.success("no_speech", out(Finality.FINAL, Reliability.NONE, None), basis)
    if event.kind is SttKind.PARTIAL:
        return run.success("transcribed", out(Finality.PARTIAL, Reliability.LOW, event.text), basis)
    if event.confidence is not None and event.confidence < MIN_CONFIDENCE:
        return partial(run, "transcript_uncertain", out(Finality.FINAL, Reliability.LOW, event.text), basis)
    return run.success("transcribed", out(Finality.FINAL, Reliability.HIGH, event.text), basis)
