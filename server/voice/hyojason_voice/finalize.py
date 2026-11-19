"""N-06 finalize_instruction_turn: 활성 통화의 확정 발화만 한 번 커밋한다.

부분 인식·중복·역순·종료 후 도착을 거른다. 부모 발화도 커밋은 하지만 triggered=False라
지시·목표·타이머를 만들지 않는다(E07).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus

from .consent import ConsentView
from .stt import Finality


@dataclass(frozen=True)
class FinalizeRequest:
    call_id: str
    turn_id: str
    utterance_seq: int
    speaker_role: Role
    finality: Finality
    text: str | None = field(repr=False)
    ended_at: float | None
    consent_revision: int  # 이 발화의 STT를 시작할 때 잠근 동의 버전


@dataclass(frozen=True)
class FinalUtterance:
    utterance_id: str | None
    utterance_seq: int
    speaker_role: Role
    text: str | None = field(repr=False)
    triggered: bool  # True면 N-15 해석을 시작한다 (자녀 발화만)


@dataclass
class TurnFinalizer:
    """통화 하나의 발화 확정 상태. call.ended·동의 철회 시 close()."""

    pair_id: str
    call_id: str
    seen: set[str] = field(default_factory=set)
    last_seq: dict[Role, int] = field(default_factory=dict)
    closed: bool = False

    def close(self) -> None:
        self.closed = True

    def finalize(
        self, req: FinalizeRequest, consent: ConsentView, *, trace_id: str, source: Source = Source.LIVE
    ) -> NodeResult[FinalUtterance]:
        """consent는 지금 시점의 최신 동의. 철회됐거나 STT 시작 뒤 동의가 바뀌었으면 폐기한다."""
        run = NodeRun("N-06", trace_id, source)
        basis = Basis(
            pair_id=self.pair_id,
            call_id=self.call_id,
            consent_revision=req.consent_revision,
            utterance_seq=req.utterance_seq,
        )

        if self.closed or req.call_id != self.call_id:
            return run.error(ErrorCode.STALE_BASIS, "check_order", basis)
        if not consent.stt_allowed or req.consent_revision != consent.consent_revision:
            return run.error(ErrorCode.STALE_BASIS, "check_consent", basis)
        if req.finality is Finality.PARTIAL:
            run.step("check_order", StepStatus.SKIPPED, "PARTIAL")
            return run.success(
                "partial_ignored",
                FinalUtterance(None, req.utterance_seq, req.speaker_role, None, False),
                basis,
            )
        if not req.text:
            return run.error(ErrorCode.INVALID_INPUT, "check_order", basis)

        utterance_id = f"{self.call_id}:{req.turn_id}"
        if utterance_id in self.seen:
            run.step("check_order", StepStatus.SKIPPED, "DUPLICATE")
            return run.success(
                "duplicate_ignored",
                FinalUtterance(utterance_id, req.utterance_seq, req.speaker_role, None, False),
                basis,
            )
        if req.utterance_seq <= self.last_seq.get(req.speaker_role, -1):
            return run.error(ErrorCode.STALE_BASIS, "check_order", basis)
        run.step("check_order", StepStatus.SUCCESS)

        self.seen.add(utterance_id)
        self.last_seq[req.speaker_role] = req.utterance_seq
        utt = FinalUtterance(
            utterance_id, req.utterance_seq, req.speaker_role, req.text, req.speaker_role is Role.CHILD
        )
        return run.success("final_committed", utt, basis)
