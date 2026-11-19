"""MOCK of 아직 없는 도움 단위 저장소들.

- NAVIGATION_TRAIL: N-12 record_verified_navigation (D)
- INFERENCE_RESULTS: B-05 추론 후보·제안·판단 결과 (C)
- PENDING_UTTERANCES: B-02 대기 중인 확정 발화 (B)

실제 저장소가 생기면 같은 HelpScopedStore 인터페이스(scope, purge)를 구현해 교체한다.
"""

from __future__ import annotations

from collections import defaultdict

from ..common.enums import CleanupScope


class ScopedBuffer:
    def __init__(self, scope: CleanupScope, *, fail_on_purge: bool = False) -> None:
        self.scope = scope
        self._items: dict[str, list[str]] = defaultdict(list)
        self.fail_on_purge = fail_on_purge  # 정리 실패 경로 시험용

    def put(self, help_id: str, item_ref: str) -> None:
        """원문이 아닌 참조 ID만 넣는다 (계약: 원문·좌표 영속 금지)."""
        self._items[help_id].append(item_ref)

    def count(self, help_id: str) -> int:
        return len(self._items.get(help_id, ()))

    def purge(self, help_id: str) -> None:
        if self.fail_on_purge:
            raise RuntimeError(f"{self.scope} purge failed")
        self._items.pop(help_id, None)
