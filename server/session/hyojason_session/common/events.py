"""제어 이벤트 봉투 (docs/contracts.md '이벤트와 전송').

모든 제어 이벤트는 v, type, event_id, pair_id, call_id, help_id, payload를 갖는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

EVENT_VERSION = 1


@dataclass(frozen=True)
class ControlEvent:
    v: int
    type: str
    event_id: str
    pair_id: str
    call_id: str | None
    help_id: str | None
    payload: Mapping[str, Any]
