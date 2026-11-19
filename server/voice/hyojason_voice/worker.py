"""STT worker: 통화 방에 숨은 참가자로 들어가 참가·퇴장을 보고, 역할별 마이크 트랙을 RTZR로 인식한다.

보통은 app이 통화 수락 때 자동으로 띄우고 종료 때 취소한다(app._start_worker).
상태를 바꾸는 일(N-03 JOINED/END, N-06 확정)은 전부 app이 넘긴 CallHooks로 한다 → 같은 프로세스·같은 이벤트 루프.

dev 단독 실행 (콘솔 출력만, 서버 상태는 안 바꿈):
    .venv/bin/python -m hyojason_voice.worker <call_id> [--print-text] [--no-keywords]

- 트랙마다 RTZR 스트림을 따로 연다(참가자가 둘이라 AgentSession 하나로는 역할을 못 나눈다).
- 인식 원문은 로그에 쓰지 않는다(B02.2). 단독 실행의 --print-text만 예외(dev 확인용).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from hyojason_session.common.enums import Role
from hyojason_session.common.result import Status, new_id
from livekit import api as lk
from livekit import rtc
from livekit.agents import stt as lkstt
from livekit.plugins import rtzr

from .audio import AudioChunk, AudioIngest, SourceKind
from .common import parse_identity
from .consent import ConsentView
from .stt import Finality, Reliability, SttEvent, SttKind, TurnRequest, transcribe_korean_turn

log = logging.getLogger("hyojason.worker")

WORKER_IDENTITY = "stt-worker"
STT_DEADLINE_MS = 3000
STT_RESTART_S = 5.0  # RTZR 스트림이 죽은 뒤 다시 여는 최소 간격 (인증 실패 등에서 재연결 폭주 방지)
REQUIRED_ENV = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "RTZR_CLIENT_ID", "RTZR_CLIENT_SECRET")
# RTZR 키워드 부스팅: sommers_ko 스트리밍 전용, 한글·공백만, 100개·20자 이하. 숫자는 "여섯 시"처럼 쓴다.
# 목록은 C의 화면 대상 이름·별칭(server/agent/profiles.py TARGETS)과 목표·슬롯 단어에서 왔다 (#22).
# server/tests/test_rtzr_keywords.py가 C 대상 이름 누락과 RTZR 제약을 확인한다.
KEYWORDS = [
    # 앱과 화면 대상 (C TARGETS의 한글 이름·별칭)
    "설정 앱", "설정", "시계 앱", "시계", "지도 앱", "구글 지도", "지도", "메시지 앱", "문자 앱",
    "지메일", "메일 앱", "디스플레이", "화면 설정", "표시 크기 및 텍스트", "글자 크기", "폰트 크기",
    "화면 자동 꺼짐", "자동 꺼짐", "화면 꺼짐", "십 분", "글자 크게", "큰 쪽", "알람 탭", "알람 추가",
    "플러스", "추가 버튼", "알람 시각", "저장 버튼", "저장", "검색창", "목적지 검색 입력",
    "일치하는 시연 장소", "길찾기", "새 문자", "새 대화", "승인된 시연 상대", "문자 입력창",
    "보내기 버튼", "보내기", "받은편지함", "시연 메일", "테스트 메일",
    # 목표와 슬롯 (N-15 목표 확률·goal_slots)
    "알람", "문자", "메시지", "메일", "글씨", "단계", "오전", "오후", "아침", "저녁", "내일", "오늘",
    "여섯 시", "일곱 시", "반", "서울역", "국립중앙박물관", "시연 장소", "시연 상대", "시연 문구",
    # B 초안에 있던 일반 설정어
    "와이파이", "블루투스", "알림",
]

_SOURCE = {
    rtc.TrackSource.SOURCE_MICROPHONE: SourceKind.MICROPHONE,
    rtc.TrackSource.SOURCE_CAMERA: SourceKind.CAMERA,
    rtc.TrackSource.SOURCE_SCREENSHARE: SourceKind.SCREEN_SHARE,
}


@dataclass(frozen=True)
class CallHooks:
    """worker → app. 모두 동기 함수라 이벤트 루프 안에서 한 번에 끝난다(app의 상태 잠금 규칙)."""

    joined: Callable[[Role], None]
    left: Callable[[Role], None]
    consent: Callable[[], ConsentView]  # 지금 시점의 동의
    # (role, turn_id, utterance_seq, text, ended_at, 이 발화 STT 시작 때 잠근 consent_revision)
    final: Callable[[Role, str, int, str, float, int], None]


def missing_env() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


def _worker_token(call_id: str) -> str:
    grants = lk.VideoGrants(
        room_join=True, room=call_id, can_subscribe=True, can_publish=False, can_publish_data=False, hidden=True
    )
    return lk.AccessToken().with_identity(WORKER_IDENTITY).with_grants(grants).to_jwt()  # 키는 환경변수


async def _recognize(role: Role, call_id: str, stream, hooks: CallHooks, turns: Iterator[int]) -> None:
    """RTZR 이벤트 → SttEvent → N-05 → (확정·신뢰 높음만) hooks.final.

    turns는 트랙 하나에 하나. 스트림을 새로 열어도 번호가 이어져야 N-06이 중복·역순으로 버리지 않는다.
    """
    turn = 0
    locked_rev: int | None = None  # 발화의 첫 이벤트에서 잠근 동의 버전 (N-06이 대조)
    try:
        async for ev in stream:
            if ev.type is lkstt.SpeechEventType.FINAL_TRANSCRIPT:
                kind = SttKind.FINAL
            elif ev.type is lkstt.SpeechEventType.INTERIM_TRANSCRIPT:
                kind = SttKind.PARTIAL
            else:
                continue
            if locked_rev is None:
                locked_rev = hooks.consent().consent_revision
            if kind is SttKind.PARTIAL:
                continue  # 부분 인식은 다음 노드로 보내지 않는다
            turn = next(turns)
            text = ev.alternatives[0].text if ev.alternatives else None
            # RTZR 스트리밍은 confidence를 주지 않는다 → None (N-05 MIN_CONFIDENCE 판정이 안 걸림)
            req = TurnRequest(f"{call_id}/{role.value}", role, call_id, f"{role.value}-{turn}", STT_DEADLINE_MS)
            r = transcribe_korean_turn(req, SttEvent(kind, text, None, time.time()), trace_id=new_id("trace"))
            log.info("[%s] %s (%d자)", role.value, r.outcome, len(text or ""))
            if r.status is Status.SUCCESS and r.data.finality is Finality.FINAL and r.data.reliability is Reliability.HIGH:
                hooks.final(role, req.turn_id, turn, r.data.text, r.data.ended_at, locked_rev)
            locked_rev = None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # RTZR 연결·인증 실패 등 → 이 역할의 STT만 멈추고 통화는 그대로
        req = TurnRequest(f"{call_id}/{role.value}", role, call_id, f"{role.value}-{turn}", STT_DEADLINE_MS)
        r = transcribe_korean_turn(req, SttEvent(SttKind.ERROR), trace_id=new_id("trace"))
        log.warning("[%s] STT 중단 %s: %s", role.value, r.diagnostics.terminal_error.code, type(e).__name__)


async def run(call_id: str, pair_id: str, hooks: CallHooks, *, keywords: bool = True) -> None:
    """취소되거나 방에서 끊길 때까지 돈다."""
    engine = rtzr.STT(model="sommers_ko", keywords=KEYWORDS if keywords else None)
    room = rtc.Room()
    ingest = AudioIngest(pair_id, call_id)
    tasks: list[asyncio.Task] = []
    done = asyncio.Event()

    def role_of(who: rtc.RemoteParticipant) -> Role | None:
        parsed = parse_identity(who.identity)
        return parsed[1] if parsed and parsed[0] == pair_id else None  # 다른 페어·모르는 참가자 무시

    async def pump(track: rtc.Track, pub: rtc.RemoteTrackPublication, who: rtc.RemoteParticipant, role: Role):
        turns = itertools.count(1)
        stream, rec, opened = None, None, -STT_RESTART_S
        seq = 0
        async for fe in rtc.AudioStream(track):
            chunk = AudioChunk(call_id, track.sid, who.identity, _SOURCE.get(pub.source, SourceKind.UNKNOWN),
                               fe.frame.data.tobytes(), seq)
            seq += 1
            r = ingest.ingest(chunk, stt_allowed=hooks.consent().stt_allowed, trace_id="worker")
            if r.outcome not in ("audio_ready", "audio_gap"):
                continue  # 동의 없음·영상·중복 트랙·종료 후 → STT로 보내지 않는다
            ingest.drain(role)  # 큐는 N-04 계약용. 프레임은 바로 STT로 넘긴다
            if rec is None or rec.done():
                # RTZR가 웹소켓을 먼저 닫으면 plugin이 재시도하지 않고 스트림이 죽는다 → 새 스트림으로 이어 간다
                if time.monotonic() - opened < STT_RESTART_S:
                    continue  # 다시 열 때까지 들어온 프레임은 버린다
                if rec is not None:
                    log.info("[%s] STT 스트림 다시 열기", role.value)
                stream, opened = engine.stream(), time.monotonic()
                rec = asyncio.create_task(_recognize(role, call_id, stream, hooks, turns))
                tasks.append(rec)
            stream.push_frame(fe.frame)
        if stream is not None:
            stream.end_input()

    @room.on("participant_connected")
    def _on_join(who: rtc.RemoteParticipant):
        if role := role_of(who):
            hooks.joined(role)

    @room.on("participant_disconnected")
    def _on_leave(who: rtc.RemoteParticipant):
        if role := role_of(who):
            hooks.left(role)

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, pub: rtc.RemoteTrackPublication, who: rtc.RemoteParticipant):
        role = role_of(who)
        if track.kind != rtc.TrackKind.KIND_AUDIO or role is None:
            return  # 영상·모르는 참가자는 무시 (N-04)
        log.info("[%s] 트랙 구독, STT 시작", role.value)
        tasks.append(asyncio.create_task(pump(track, pub, who, role)))

    @room.on("disconnected")
    def _on_disconnected(*_):
        done.set()

    try:
        await room.connect(os.environ["LIVEKIT_URL"], _worker_token(call_id))
        log.info("방 %s 참가", call_id)
        for who in list(room.remote_participants.values()):  # worker보다 먼저 들어온 참가자
            _on_join(who)
        await done.wait()
    finally:
        ingest.close()  # 남은 바이트 폐기 (B02.3)
        for t in tasks:
            t.cancel()
        await room.disconnect()
        await engine.aclose()


def main() -> None:
    p = argparse.ArgumentParser(description="dev 단독 실행: 콘솔 출력만, 서버 상태는 안 바꿈")
    p.add_argument("call_id")
    p.add_argument("--print-text", action="store_true", help="인식 원문 출력 (dev 전용)")
    p.add_argument("--no-keywords", action="store_true", help="키워드 부스팅 끄기 (정확도 비교용)")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    log.setLevel(logging.INFO)

    def final(role, turn_id, seq, text, ended_at, rev):
        print(f"[{role.value}] FINAL {turn_id}" + (f": {text}" if a.print_text else f" ({len(text)}자)"), flush=True)

    hooks = CallHooks(
        joined=lambda r: print(f"[{r.value}] 참가", flush=True),
        left=lambda r: print(f"[{r.value}] 퇴장", flush=True),
        consent=lambda: ConsentView(True, True, False, False, 0),  # 단독 실행은 동의를 모른다 → 허용으로 본다
        final=final,
    )
    pair_id = os.environ.get("PAIR_ID") or input("pair_id (dev_call.sh 출력의 PID): ").strip()
    try:
        asyncio.run(run(a.call_id, pair_id, hooks, keywords=not a.no_keywords))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
