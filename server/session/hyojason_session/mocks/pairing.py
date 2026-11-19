"""MOCK of N-01 resolve_pair · N-02 resolve_consent · N-03 start_or_close_call (B 담당).

이슈 #10 제안대로 B가 판단 함수를 주면 제어 서버(api.hub)가 이 자리에 연결한다. 그 전까지:
- 페어 승인: 인증된 토큰의 페어를 그대로 승인한다.
- 통화 수락: LiveKit 참가 확인 없이 곧바로 RTC 연결(call.connected)로 본다.
- RTC 토큰: 발급하지 않는다(null). 실제 음성은 B의 LiveKit 연동 뒤에 가능하다.
모든 응답은 source=FIXTURE다.
"""

from __future__ import annotations

from ..common.types import ActorContext


class MockPairingService:
    source = "FIXTURE"

    def claim(self, actor: ActorContext) -> dict:
        return {"pair_id": actor.pair_id, "approved": True, "source": self.source}

    def rtc_connected_on_accept(self) -> bool:
        """실제로는 두 참가자가 LiveKit 룸에 들어온 것을 N-03이 확인한 뒤 call.connected를 낸다."""
        return True

    def rtc_token(self, actor: ActorContext, call_id: str) -> dict:
        return {"call_id": call_id, "rtc_token": None, "source": self.source}
