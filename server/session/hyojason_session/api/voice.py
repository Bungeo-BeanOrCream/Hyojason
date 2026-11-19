"""B 음성 서비스 어댑터 — #10 합의: B는 판단 함수(server/voice hyojason_voice.service), D는 라우트·도움 세션.

MockTokenAuthenticator·MockPairingService 자리에 그대로 들어간다(같은 메서드 이름). B 코드는 고치지 않고 부르기만 한다.

| D가 쓰는 것 | B 함수 |
|---|---|
| 인증 `authenticate`·`role_of` | 같은 이름 (B-01 기기 토큰) |
| 이벤트 `attach(hub)` | `set_event_sink(hub.on_voice_event)` — call.invite/accepted/connected/ended, speech.final |
| 통화 `start_call`·`accept_call`·`end_call`·`joined` | `call_op(Operation.START/ACCEPT/END/JOINED)` (N-03) |
| `current_call`·`rtc_token`·`livekit_webhook` | 같은 이름 |
| 페어 `invite`·`claim`·`unpair` | 같은 이름 (N-01) |
| 동의 `get_consent`·`set_consent`·`consenting_roles` | 같은 이름, 역할별 동의는 `consents[pair].enabled` (N-02) |
| dev `dev_speech_final` | 같은 이름 (STT 없이 N-06을 거친 확정 발화) |

B 오류(ServiceError, 오류 NodeResult)는 VoiceError로 올린다. 제어 서버는 B 경로 응답을 B 앱이 쓰던 모양
(HTTP 상태와 {"detail": 코드}, server/voice/hyojason_voice/app.py)으로 돌려준다.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import ModuleType

from ..common.enums import Role
from ..common.types import ActorContext

# B app.py의 _HTTP와 같은 표 (B 앱의 오류 처리를 바꾸지 않기 위해)
HTTP_STATUS = {
    "UNAUTHORIZED": 403, "INVALID_INPUT": 422, "DUPLICATE": 409, "STALE_BASIS": 409,
    "ALREADY_ENDED": 409, "NO_CONSENT": 409, "TIMEOUT": 408, "DEPENDENCY_UNAVAILABLE": 503,
}


class VoiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 400)


def _code(value) -> str:
    return getattr(value, "value", value)


class BVoiceService:
    """B의 hyojason_voice.service 모듈을 D 제어 서버 모양으로 감싼다."""

    source = "LIVE"

    def __init__(self, service: ModuleType, operation, capability) -> None:
        self._s = service
        self._op = operation
        self._cap = capability

    @classmethod
    def from_import(cls) -> BVoiceService:
        """server/voice가 PYTHONPATH에 있어야 한다 (B의 requirements.txt: livekit-api 등)."""
        service = importlib.import_module("hyojason_voice.service")
        call = importlib.import_module("hyojason_voice.call")
        consent = importlib.import_module("hyojason_voice.consent")
        return cls(service, call.Operation, consent.Capability)

    # --- 공통 -------------------------------------------------------------------
    def _run(self, fn: Callable, *args):
        try:
            return fn(*args)
        except self._s.ServiceError as error:
            raise VoiceError(_code(error.code)) from None

    def _node(self, result):
        if _code(result.status) == "error":
            raise VoiceError(_code(result.diagnostics.terminal_error.code))
        return result

    # --- 인증 (B-01) · 이벤트 ------------------------------------------------------
    def authenticate(self, token: str | None) -> ActorContext | None:
        return self._s.authenticate(token)

    def role_of(self, pair_id: str, user_id: str) -> Role | None:
        return self._s.role_of(pair_id, user_id)

    def attach(self, hub) -> None:
        self._s.set_event_sink(hub.on_voice_event)

    # --- 통화 (N-03) ---------------------------------------------------------------
    def rtc_connected_on_accept(self) -> bool:
        return False  # 양쪽이 실제로 방에 들어오면 B의 worker·웹훅이 JOINED → call.connected를 낸다

    def start_call(self, actor: ActorContext):
        return self._node(self._run(self._s.call_op, self._op.START, actor))

    def accept_call(self, actor: ActorContext, call_id: str):
        return self._node(self._run(self._s.call_op, self._op.ACCEPT, actor, call_id))

    def end_call(self, actor: ActorContext, call_id: str):
        return self._node(self._run(self._s.call_op, self._op.END, actor, call_id))

    def joined(self, actor: ActorContext, call_id: str):
        return self._node(self._run(self._s.call_op, self._op.JOINED, actor, call_id))

    def current_call(self, actor: ActorContext) -> dict | None:
        return self._run(self._s.current_call, actor)

    def rtc_token(self, actor: ActorContext, call_id: str) -> dict:
        return self._run(self._s.rtc_token, actor, call_id)

    def livekit_webhook(self, body: str, authorization: str | None) -> dict:
        return self._run(self._s.livekit_webhook, body, authorization)

    # --- 페어 (N-01) ---------------------------------------------------------------
    def invite(self, authorization: str | None, role: Role) -> dict:
        return self._run(self._s.invite, authorization, role)

    def claim(self, authorization: str | None, role: Role, invite_code: str | None, pair_id: str | None):
        result, token = self._run(self._s.claim, authorization, role, invite_code, pair_id)
        return self._node(result), token

    def unpair(self, actor: ActorContext) -> None:
        self._run(self._s.unpair, actor)

    # --- 동의 (N-02) ---------------------------------------------------------------
    def get_consent(self, actor: ActorContext):
        return self._run(self._s.get_consent, actor)

    def set_consent(self, actor: ActorContext, enabled: bool, consent_revision: int,
                    capabilities: list[str], call_id: str | None):
        caps = frozenset(self._cap(c) for c in capabilities)
        return self._node(self._run(self._s.set_consent, actor, enabled, consent_revision, caps, call_id))

    def consenting_roles(self, pair_id: str) -> set[Role]:
        """N-11에 넣을 역할별 분석 동의. B 공개 함수에 역할별 값이 없어 ConsentState.enabled를 읽는다."""
        state = self._s.consents.get(pair_id)
        return {Role(_code(role)) for role, on in (state.enabled.items() if state else ()) if on}

    # --- dev ---------------------------------------------------------------------
    def dev_speech_final(self, actor: ActorContext, call_id: str, speaker_role: Role, text: str):
        return self._node(self._run(self._s.dev_speech_final, actor, call_id, speaker_role, text))
