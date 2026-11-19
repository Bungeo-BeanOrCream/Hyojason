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
| `hyojason_session/help_state.py` | **N-11** 통화·도움·동의·화면 상태와 권위 버전 |
| `hyojason_session/navigation.py` | **N-12** 관찰 전이, 공백, 지시·위치·수행 이력 |
| `hyojason_session/control.py` | 통화별 쓰기 경로와 N-13의 `HelpStateReader` 구현 |
| `hyojason_session/role_view.py` | **N-21** project_role_view |
| `hyojason_session/reconnect.py` | **N-13** reconcile_reconnect |
| `hyojason_session/completion.py` | **N-22** resolve_arrival_or_completion + 도착·종료 장부(CompletionLedger) |
| `hyojason_session/purge.py` | **N-23** purge_help_and_restart + 종료 기록(TombstoneRegistry) |
| `hyojason_session/session_state.py` | N-11 저장소·N-22 장부·N-23 기록을 묶은 읽기 어댑터 (N-13 입력) |
| `hyojason_session/demo_fixtures.py` | **N-24** AVD·앱·시나리오 준비 점검 |
| `hyojason_session/demo_evaluation.py` | **N-25** V-01~V-07 판정과 V-08 모델·비용 확인 |
| `hyojason_session/demo_verify.py` | N-24·N-25 JSON 실행 도구 ([예시와 리허설 절차](../../demo/fixtures/README.md)) |
| `hyojason_session/views.py` | RoleSnapshot·ParentView·ChildView ([제안서](../../docs/work/D-role-view-contract.md)) |
| `hyojason_session/client_resume_gate.py` | N-13 클라이언트 규칙 참조 구현 (Android Kotlin 이식용, 서버에서 실행 안 함) |
| `hyojason_session/client_cleanup.py` | N-23 클라이언트 규칙 참조 구현 (부모 로컬 우선 정리, 완료 문구 1회) |
| `hyojason_session/ports.py` | 다른 노드 경계 (PairDirectory, HelpStateReader, HelpScopedStore, EventSink) |
| `hyojason_session/mocks/` | **MOCK**: 격리 테스트용 N-11 상태, N-12·B-05·B-02 저장소, WS 제어 채널, N-01 페어 확인 |
| `schemas/` | 공유 JSON Schema (이슈 #2) |
| `tests/` | 노드 문서의 케이스 ID(13.x, 21.x, 22.x, 23.x, B07.x)를 테스트 이름에 사용 |

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

## 도움 상태와 관찰 이력 쓰기

제어 서버는 인증된 통화마다 `SessionControl`을 하나 생성한다. `apply(event, actor, basis)`로 인증 계층이 얻은 역할과 범위, 이벤트, 계산 시작 시점의 `Basis`를 받는다. `update.state`에는 N-11 결과가 있고, `update.navigation`에는 N-12가 호출된 경우 그 결과가 있다. 상태가 성공적으로 적용된 뒤 `side_effects`를 발행한다. N-13에는 같은 객체를 `store`로 넘긴다.

| 입력 경계 | 이벤트 또는 값 |
|---|---|
| 양쪽 통화와 분석 동의 | `call.connected`, `call.reconnecting`, `call.ended`, 역할별 `consent.updated` |
| 부모 관찰 | `projection.started`, `screen.observed`. 연속 전이를 입증하면 `from_screen_revision`과 승인된 근거를 함께 전달 |
| 자녀 발화와 해석 | `speech.final`, 같은 발화의 `instruction.drafted` 및 `goal.drafted` |
| 화면 판정과 종료 | `target.located`, `action.assessed`, `arrival.suspected` 또는 `arrival.revoked`, `help.complete` 및 `help.stop` |

프로필은 `NavigationCatalog`에 주입한다. `approved_slots`에는 목표별로 허용하는 서버 검증 함수를 전달한다. [이슈 #3 화면 관찰 계약](../../docs/screen-observation-profile.md)

서버 간 JSON 대조에는 공통 `to_jsonable`과 `schemas/`를 사용한다. 테스트는 `FIXTURE`로 합성 상위 이벤트를 전달한다. 오디오 전환 평가, STT 종료, 캡처 재관찰은 발행 효과로 검증한다. 실제 오디오, AVD 캡처·표시, Zoro 추론은 담당 노드와 연동해 검증한다.
