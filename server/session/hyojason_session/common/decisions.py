"""B-05 판단 결과 중 D 노드가 소비하는 타입 (docs/contracts.md 자료형 표)."""

from __future__ import annotations

from dataclasses import dataclass

from .result import Basis


@dataclass(frozen=True)
class ArrivalDecision:
    """N-18(C)이 통화 후 화면 증거로 낸 도착 추정. N-22가 검증해 자녀 알림으로 바꾼다."""

    arrival_id: str
    arrived: bool
    goal_revision: int
    evidence_codes: tuple[str, ...]
    basis: Basis
