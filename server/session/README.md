# server/session · 효자손 제어 서버 (D)

세션·도움 상태·역할별 투영을 맡는다. 설계 기준은 [`docs/`](../../docs/README.md), 작업 상태는 [`docs/work/D-status.md`](../../docs/work/D-status.md).

## 실행

```bash
cd server/session
python -m pip install "pytest>=8"   # 테스트에만 필요. pyproject의 pythonpath 설정으로 설치 없이 import
python -m pytest
python -m hyojason_session.export_schemas          # 스키마 갱신
python -m hyojason_session.export_schemas --check  # 코드와 스키마 일치 확인
```

Python 3.11 이상. 런타임 외부 의존성은 없다.

## 구조

| 경로 | 내용 |
|---|---|
| `hyojason_session/common/` | 공통 계약: NodeResult·Diagnostic·Basis, 상태 자료형, 열거형, JSON 변환·스키마 생성 |
| `hyojason_session/role_view.py` | **N-21** project_role_view |
| `hyojason_session/reconnect.py` | **N-13** reconcile_reconnect |
| `hyojason_session/views.py` | RoleSnapshot·ParentView·ChildView ([제안서](../../docs/work/D-role-view-contract.md)) |
| `hyojason_session/client_resume_gate.py` | N-13 클라이언트 규칙 참조 구현 (Android Kotlin 이식용, 서버에서 실행 안 함) |
| `hyojason_session/ports.py` | 다른 노드 경계 (PairDirectory, HelpStateReader) |
| `hyojason_session/mocks/` | **MOCK**: N-11·N-22·N-23 상태 저장, N-01 페어 확인 |
| `schemas/` | 공유 JSON Schema (이슈 #2) |
| `tests/` | 노드 문서의 케이스 ID(13.x, 21.x)를 테스트 이름에 사용 |

## 노드 호출 예

```python
from hyojason_session.reconnect import ReconnectRequest, reconcile_reconnect

result = reconcile_reconnect(
    ReconnectRequest(pair_id, help_id, role, last_event_seq, snapshot_request_id),
    actor=actor,        # 인증 토큰에서 얻은 ActorContext
    pairs=pairs,        # PairDirectory (현재 Mock)
    store=store,        # HelpStateReader (현재 Mock)
    trace_id=trace_id,
)
# result.status: success | error, result.outcome: snapshot_ready | no_active_help | None
```

HTTP/WS 엔드포인트(`GET /v1/help/current`, `WS /v1/events`)는 아직 없다. 노드 함수는 프레임워크와 무관하게 호출할 수 있다.
