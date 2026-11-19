"""MOCK — 아직 구현되지 않은 노드를 대신하는 메모리 구현.

실제 노드가 들어오면 같은 인터페이스(hyojason.session.ports)로 교체한다.
Mock을 거친 결과는 실제 라이브 성공과 분리해 source=FIXTURE로 기록한다 (work-plan.md).
"""

from .help_store import InMemoryHelpStore
from .pair_directory import StaticPairDirectory

__all__ = ["InMemoryHelpStore", "StaticPairDirectory"]
