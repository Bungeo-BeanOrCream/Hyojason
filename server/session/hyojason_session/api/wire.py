"""제어 채널의 메시지 형식과 역할별 허용 범위.

- 봉투: docs/contracts.md의 v, type, event_id, pair_id, call_id, help_id, payload (7키, 그 외 거절).
  trace_id 필드는 A·B 합의 전이라 아직 싣지 않는다 (계약 추가 제안 6장).
- 오류 코드: docs/implementation-contract.md의 FORBIDDEN / INVALID_EVENT / NOT_FOUND / CONFLICT.
  토큰이 없거나 틀리면 UNAUTHENTICATED(401).
- D가 정의한 채널 메시지(계약 추가 제안 7장): help.view, event.ack, event.rejected, ping/pong.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..common.enums import HelpCommand, Role
from ..common.events import EVENT_VERSION, ControlEvent
from ..common.result import new_id
from ..common.serialize import to_jsonable
from ..common.types import ActorContext

ENVELOPE_KEYS = frozenset({"v", "type", "event_id", "pair_id", "call_id", "help_id", "payload"})

# 앱이 WS로 보낼 수 있는 이벤트. 가족 역할이 아니라 이번 도움의 지원 대상 기준이다 (#6, 기본 부모).
ASSISTED_EVENTS = frozenset({
    "consent.updated", "projection.started", "screen.observed",
    "guide.shown", "guide.suppressed", "help.stop",
})
COUNTERPART_EVENTS = frozenset({"consent.updated", "help.complete"})
COMMANDS: Mapping[str, HelpCommand] = {"help.complete": HelpCommand.COMPLETE, "help.stop": HelpCommand.STOP}

# 지원 대상의 상대 기기에 보낼 수 있는 이벤트만 허용한다 (N-21: 목표·지시·화면·이력·좌표 금지)
COUNTERPART_DELIVERABLE = frozenset({"call.invite", "help.view", "arrival.suspected", "arrival.revoked", "help.ended"})

VIEW_EVENT = "help.view"
ACK = "event.ack"
REJECTED = "event.rejected"
PING = "ping"
PONG = "pong"

HTTP_STATUS = {"UNAUTHENTICATED": 401, "FORBIDDEN": 403, "INVALID_EVENT": 400, "NOT_FOUND": 404, "CONFLICT": 409}


class WireError(Exception):
    def __init__(self, api_code: str, node_code: str | None = None) -> None:
        super().__init__(api_code)
        self.api_code = api_code
        self.node_code = node_code


def api_code_for(node_code: str | None) -> str:
    if node_code == "UNAUTHORIZED":
        return "FORBIDDEN"
    if node_code == "INVALID_INPUT":
        return "INVALID_EVENT"
    return "CONFLICT"  # STALE_BASIS, ALREADY_ENDED, NOT_APPLICABLE, NO_CONSENT, CAPTURE_GAP 등


def _id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and not any(c.isspace() for c in value)


def parse_envelope(message: object, actor: ActorContext) -> dict:
    """봉투 구조와 페어만 본다. 역할별 허용 여부는 check_sender가 이번 도움의 지원 대상으로 본다."""
    if not isinstance(message, dict) or set(message) != ENVELOPE_KEYS:
        raise WireError("INVALID_EVENT")
    if message["v"] != EVENT_VERSION or not isinstance(message["type"], str):
        raise WireError("INVALID_EVENT")
    if not _id(message["event_id"]) or not _id(message["call_id"]) or not isinstance(message["payload"], dict):
        raise WireError("INVALID_EVENT")
    if message["help_id"] is not None and not _id(message["help_id"]):
        raise WireError("INVALID_EVENT")
    if message["pair_id"] != actor.pair_id:
        raise WireError("FORBIDDEN", "UNAUTHORIZED")
    return message


def check_sender(envelope: Mapping, actor: ActorContext, assisted_role: Role) -> None:
    allowed = ASSISTED_EVENTS if actor.role is assisted_role else COUNTERPART_EVENTS
    if envelope["type"] not in allowed:
        raise WireError("FORBIDDEN", "UNAUTHORIZED")


def control_event(type_: str, pair_id: str, call_id: str | None, help_id: str | None,
                  payload: Mapping[str, Any]) -> ControlEvent:
    return ControlEvent(v=EVENT_VERSION, type=type_, event_id=new_id("evt"), pair_id=pair_id,
                        call_id=call_id, help_id=help_id, payload=dict(payload))


def ack(event_id: object, outcome: str | None) -> dict:
    return {"v": EVENT_VERSION, "type": ACK, "event_id": event_id, "outcome": outcome}


def rejected(event_id: object, api_code: str, node_code: str | None = None) -> dict:
    return {"v": EVENT_VERSION, "type": REJECTED, "event_id": event_id, "code": api_code, "node_code": node_code}


def effect_payload(kind: str, snapshot, arrival_id: str | None) -> dict | None:
    """N-11 효과 → 앱으로 보낼 payload. 서버 내부 효과면 None.

    snapshot은 효과를 낸 직후의 N-11 HelpSnapshot이다. 부모 이벤트에는 표시 전 재검사에 쓰는
    Basis(식별자·버전뿐)를 싣고, 자녀 이벤트에는 싣지 않는다.
    """
    basis = to_jsonable(snapshot.basis)
    if kind in ("arrival.suspected", "arrival.revoked"):
        return {"arrival_id": arrival_id}
    if kind == "instruction.accepted" and snapshot.instruction is not None:
        i = snapshot.instruction
        return {"instruction_id": i.instruction_id, "instruction_revision": i.instruction_revision,
                "action": i.action, "target_key": i.target_key,
                "origin_screen_revision": i.origin_screen_revision, "basis": basis}
    if kind == "goal.updated" and snapshot.goal is not None:
        g = snapshot.goal
        return {"goal_id": g.goal_id, "goal_revision": g.goal_revision,
                "approved_slots": dict(g.approved_slots), "basis": basis}
    if kind == "mode.changed":
        return {"mode": snapshot.mode, "mode_revision": snapshot.mode_revision, "basis": basis}
    if kind == "instruction.cleared":
        return {"instruction_revision": snapshot.instruction_revision, "basis": basis}
    return {"basis": basis}


def effect_events(update, snapshot, *, arrival_id: str | None = None) -> list[tuple[Role, ControlEvent]]:
    """N-11 효과 중 앱(PARENT/CHILD/BOTH)으로 가는 것만 이벤트로 바꾼다. SERVER 효과는 내부 처리."""
    out: list[tuple[Role, ControlEvent]] = []
    for effect in update.state.data.side_effects:
        if effect.recipient == "SERVER":
            continue
        roles = (Role.PARENT, Role.CHILD) if effect.recipient == "BOTH" else (Role(effect.recipient),)
        payload = effect_payload(effect.kind, snapshot, arrival_id)
        for role in roles:
            out.append((role, control_event(effect.kind, snapshot.pair_id, snapshot.call_id,
                                            snapshot.help_id, payload)))
    return out
