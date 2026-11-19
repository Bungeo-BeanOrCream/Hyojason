"""STT worker: 통화 방에 숨은 참가자로 들어가 역할별 마이크 트랙을 RTZR로 인식한다 (N-04 → N-05).

실행 (server/voice에서, .env를 셸에 올린 뒤):
    .venv/bin/python -m hyojason_voice.worker <call_id> [--print-text] [--no-keywords]

- 트랙마다 RTZR 스트림을 따로 연다(참가자가 둘이라 AgentSession 하나로는 역할을 못 나눈다).
- 지금은 결과를 콘솔에만 찍는다. N-06(speech.final)으로 넘기는 건 다음 단계.
- 인식 원문은 --print-text일 때만 찍는다(B02.2: 원문·PCM을 로그에 남기지 않음). dev 확인용.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time

from hyojason_session.common.enums import Role
from hyojason_session.common.result import Status, new_id
from livekit import api as lk
from livekit import rtc
from livekit.agents import stt as lkstt
from livekit.plugins import rtzr

from .audio import AudioChunk, AudioIngest, SourceKind
from .common import parse_identity
from .stt import SttEvent, SttKind, TurnRequest, transcribe_korean_turn

WORKER_IDENTITY = "stt-worker"
STT_DEADLINE_MS = 3000
# RTZR 키워드 부스팅: sommers_ko 스트리밍 전용, 한글·공백만, 100개·20자 이하. 숫자는 "여섯 시"처럼 쓴다.
# ponytail: 시연 지시어 초안. C의 지시 목록이 오면 늘린다.
KEYWORDS = ["디스플레이", "화면 꺼짐", "글자 크기", "와이파이", "블루투스", "설정", "알림", "여섯 시"]

_SOURCE = {
    rtc.TrackSource.SOURCE_MICROPHONE: SourceKind.MICROPHONE,
    rtc.TrackSource.SOURCE_CAMERA: SourceKind.CAMERA,
    rtc.TrackSource.SOURCE_SCREENSHARE: SourceKind.SCREEN_SHARE,
}


def _worker_token(call_id: str) -> str:
    grants = lk.VideoGrants(
        room_join=True, room=call_id, can_subscribe=True, can_publish=False, can_publish_data=False, hidden=True
    )
    return lk.AccessToken().with_identity(WORKER_IDENTITY).with_grants(grants).to_jwt()  # 키는 환경변수


async def _recognize(role: Role, call_id: str, stream: lkstt.SpeechStream, show_text: bool) -> None:
    """RTZR 이벤트 → SttEvent → N-05. 확정문만 turn을 하나씩 올린다."""
    turn = 0
    try:
        async for ev in stream:
            if ev.type is lkstt.SpeechEventType.FINAL_TRANSCRIPT:
                kind = SttKind.FINAL
            elif ev.type is lkstt.SpeechEventType.INTERIM_TRANSCRIPT:
                kind = SttKind.PARTIAL
            else:
                continue
            text = ev.alternatives[0].text if ev.alternatives else None
            # RTZR 스트리밍은 confidence를 주지 않는다 → None (N-05 MIN_CONFIDENCE 판정이 안 걸림)
            event = SttEvent(kind, text, None, time.time())
            if kind is SttKind.FINAL:
                turn += 1
            req = TurnRequest(f"{call_id}/{role.value}", role, call_id, f"{role.value}-{turn}", STT_DEADLINE_MS)
            _print(role, transcribe_korean_turn(req, event, trace_id=new_id("trace")), kind, show_text)
    except Exception as e:  # RTZR 연결·인증 실패 등
        req = TurnRequest(f"{call_id}/{role.value}", role, call_id, f"{role.value}-{turn}", STT_DEADLINE_MS)
        r = transcribe_korean_turn(req, SttEvent(SttKind.ERROR), trace_id=new_id("trace"))
        print(f"[{role.value}] error {r.diagnostics.terminal_error.code}: {type(e).__name__}", flush=True)


def _print(role: Role, r, kind: SttKind, show_text: bool) -> None:
    if kind is SttKind.PARTIAL and not show_text:
        return  # 부분 인식은 원문 없이는 볼 게 없다
    if r.status is Status.ERROR:
        print(f"[{role.value}] error {r.diagnostics.terminal_error.code}", flush=True)
        return
    t = r.data.text or ""
    body = f": {t}" if show_text else f" ({len(t)}자)"
    print(f"[{role.value}] {r.outcome} {r.data.finality.value} {r.data.reliability.value}{body}", flush=True)


async def run(call_id: str, show_text: bool, keywords: bool) -> None:
    engine = rtzr.STT(model="sommers_ko", keywords=KEYWORDS if keywords else None)
    room = rtc.Room()
    ingest: AudioIngest | None = None
    tasks: list[asyncio.Task] = []
    done = asyncio.Event()

    async def pump(track: rtc.Track, pub: rtc.RemoteTrackPublication, who: rtc.RemoteParticipant, role: Role):
        stream = engine.stream()
        tasks.append(asyncio.create_task(_recognize(role, call_id, stream, show_text)))
        seq = 0
        async for fe in rtc.AudioStream(track):
            chunk = AudioChunk(call_id, track.sid, who.identity, _SOURCE.get(pub.source, SourceKind.UNKNOWN),
                               fe.frame.data.tobytes(), seq)
            seq += 1
            # ponytail: 동의는 아직 앱(N-02)에서 안 받아온다. N-06 연결 때 앱이 409를 주면 스트림을 닫는다.
            r = ingest.ingest(chunk, stt_allowed=True, trace_id="worker")
            if r.outcome not in ("audio_ready", "audio_gap"):
                continue
            ingest.drain(role)  # 큐는 N-04 계약용. 프레임은 바로 STT로 넘긴다
            stream.push_frame(fe.frame)
        stream.end_input()

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, pub: rtc.RemoteTrackPublication, who: rtc.RemoteParticipant):
        nonlocal ingest
        parsed = parse_identity(who.identity)
        if track.kind != rtc.TrackKind.KIND_AUDIO or parsed is None:
            return  # 영상·모르는 참가자는 무시 (N-04)
        pair_id, role = parsed
        if ingest is None:
            ingest = AudioIngest(pair_id, call_id)  # 방의 첫 참가자로 페어를 고정한다
        print(f"[{role.value}] 트랙 구독, STT 시작", flush=True)
        tasks.append(asyncio.create_task(pump(track, pub, who, role)))

    @room.on("disconnected")
    def _on_disconnected(*_):
        done.set()

    await room.connect(os.environ["LIVEKIT_URL"], _worker_token(call_id))
    print(f"방 {call_id} 참가. 말하면 확정문이 찍힙니다. 종료: Ctrl+C", flush=True)
    try:
        await done.wait()
    finally:
        if ingest:
            ingest.close()  # 남은 바이트 폐기 (B02.3)
        for t in tasks:
            t.cancel()
        await room.disconnect()
        await engine.aclose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("call_id")
    p.add_argument("--print-text", action="store_true", help="인식 원문 출력 (dev 전용)")
    p.add_argument("--no-keywords", action="store_true", help="키워드 부스팅 끄기 (정확도 비교용)")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(run(a.call_id, a.print_text, not a.no_keywords))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
