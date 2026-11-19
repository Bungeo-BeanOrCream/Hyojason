"""N-04 ingest_participant_audio: 승인된 마이크 트랙만 역할별 휘발성 큐에 넣는다.

PCM은 메모리에만 둔다. 파일·로그·진단에 쓰지 않는다(B02.2).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis, ErrorCode, NodeResult, NodeRun, StepStatus

from .common import parse_identity, partial

QUEUE_MAX_CHUNKS = 50  # 역할별 큐 길이. 넘치면 오래된 구간부터 버린다 (프레임 크기에 맞춰 조정)


class SourceKind(StrEnum):
    MICROPHONE = "MICROPHONE"
    CAMERA = "CAMERA"
    SCREEN_SHARE = "SCREEN_SHARE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class AudioChunk:
    call_id: str
    track_id: str
    participant_id: str  # LiveKit identity `{pair_id}:{role}`
    source_kind: SourceKind
    pcm: bytes = field(repr=False)
    chunk_seq: int


@dataclass(frozen=True)
class AudioIngestData:
    speaker_role: Role | None
    chunk_seq: int
    window_ref: str | None  # 로컬 큐 참조. 네트워크로 내보내지 않는다
    dropped_count: int


@dataclass
class AudioIngest:
    """통화 하나의 역할별 오디오 큐. 통화마다 새로 만든다 → call_id 격리."""

    pair_id: str
    call_id: str
    queues: dict[Role, deque[bytes]] = field(
        default_factory=lambda: {r: deque(maxlen=QUEUE_MAX_CHUNKS) for r in Role}
    )
    tracks: dict[Role, str] = field(default_factory=dict)  # 역할별 승인 마이크 트랙 1개
    last_seq: dict[str, int] = field(default_factory=dict)
    closed: bool = False

    def close(self) -> None:
        """통화 종료·동의 철회: 구독을 끊고 남은 바이트를 버린다."""
        self.closed = True
        for q in self.queues.values():
            q.clear()

    def drain(self, role: Role) -> list[bytes]:
        q = self.queues[role]
        out = list(q)
        q.clear()
        return out

    def ingest(
        self, c: AudioChunk, *, stt_allowed: bool, trace_id: str, source: Source = Source.LIVE
    ) -> NodeResult[AudioIngestData]:
        run = NodeRun("N-04", trace_id, source)
        basis = Basis(pair_id=self.pair_id, call_id=self.call_id)

        def ignored(code: str) -> NodeResult[AudioIngestData]:
            run.step("check_track", StepStatus.SKIPPED, code)
            return run.success("ignored_input", AudioIngestData(None, c.chunk_seq, None, 0), basis)

        if c.call_id != self.call_id:
            return run.error(ErrorCode.UNAUTHORIZED, "check_track", basis)
        parsed = parse_identity(c.participant_id)
        if parsed is not None and parsed[0] != self.pair_id:
            return run.error(ErrorCode.UNAUTHORIZED, "check_track", basis)

        if self.closed:
            return ignored("CLOSED")  # 종료 레이스로 늦게 온 바이트
        if parsed is None:
            return ignored("UNKNOWN_PARTICIPANT")  # STT worker 자신 등
        if c.source_kind is not SourceKind.MICROPHONE:
            return ignored("NOT_MICROPHONE")
        if not stt_allowed:
            return ignored("NO_CONSENT")
        role = parsed[1]
        if self.tracks.setdefault(role, c.track_id) != c.track_id:
            return ignored("EXTRA_TRACK")
        if c.chunk_seq <= self.last_seq.get(c.track_id, -1):
            return ignored("DUPLICATE")
        run.step("check_track", StepStatus.SUCCESS)

        self.last_seq[c.track_id] = c.chunk_seq
        q = self.queues[role]
        dropped = 1 if len(q) == q.maxlen else 0
        q.append(c.pcm)
        data = AudioIngestData(role, c.chunk_seq, f"{self.call_id}/{role.value}", dropped)
        if dropped:
            run.step("enqueue", StepStatus.SUCCESS, count=dropped)
            return partial(run, "audio_gap", data, basis)
        run.step("enqueue", StepStatus.SUCCESS)
        return run.success("audio_ready", data, basis)
