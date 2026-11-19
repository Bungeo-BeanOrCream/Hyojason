"""B가 D(N-11)로 보내는 제어 이벤트: call.invite / accepted / connected / ended, speech.final.

봉투는 D의 Event(v, type, event_id, pair_id, call_id, help_id, payload)를 그대로 쓴다.
help_id는 D가 붙이므로 None으로 보낸다.
"""

from __future__ import annotations

from hyojason_session.common.enums import Role
from hyojason_session.common.result import new_id
from hyojason_session.help_state import Event

from .finalize import FinalUtterance

MANUAL_TEST = "manual_test"  # 텍스트 입력 스텁 source. LIVE STT는 "stt"


def call_event(kind: str, pair_id: str, call_id: str, role: Role | None, reason: str | None = None) -> Event:
    assert kind in ("call.invite", "call.accepted", "call.connected", "call.ended"), kind
    payload = {"call_id": call_id, "role": role.value if role else None, "reason": reason}
    return Event(kind, new_id("evt"), pair_id, call_id, None, payload)


def speech_final_event(pair_id: str, call_id: str, utt: FinalUtterance, source: str) -> Event:
    payload = {
        "utterance_id": utt.utterance_id,
        "utterance_seq": utt.utterance_seq,
        "speaker_role": utt.speaker_role.value,
        "text": utt.text,
        "call_id": call_id,
        "source": source,
    }
    return Event("speech.final", new_id("evt"), pair_id, call_id, None, payload)
