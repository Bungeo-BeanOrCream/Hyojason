# server/session · 효자손 제어 서버 (D)

세션·도움 상태·역할별 투영을 맡는다. 설계 기준은 [`docs/`](../../docs/README.md), 작업 상태는 [`docs/work/D-status.md`](../../docs/work/D-status.md).

## 실행

```bash
cd server/session
python -m pip install "pytest>=8" "httpx>=0.27" "fastapi>=0.110" "uvicorn[standard]>=0.29"
python -m pytest
python -m hyojason_session.export_schemas          # 스키마 갱신
python -m hyojason_session.export_schemas --check  # 코드와 스키마 일치 확인
```

Python 3.11 이상. 노드 로직은 표준 라이브러리만 쓰고, 제어 서버(`hyojason_session/api`)만 FastAPI·uvicorn이 필요하다. FastAPI가 없으면 서버 테스트는 건너뛴다. WebSocket을 실제로 띄우려면 `uvicorn[standard]`(websockets 포함)가 있어야 한다.

### 제어 서버 실행

기본 실행은 B의 인증·가족 연결·동의·통화 서비스를 사용한다. LiveKit과 RTZR을 사용하려면 [B 실행 환경](../voice/README.md)의 설정을 추가한다.

```bash
cd server/session
python -m pip install -r ../voice/requirements.txt
# 격리된 로컬 예시. 운영 설정은 실행 환경에서 안전한 값으로 주입한다.
export HYOJASON_SERVICE_TOKEN="service-dev-token"
PYTHONPATH=../..:../voice python -m uvicorn hyojason_session.api.app:create_app_from_env --factory --port 8000
```

PowerShell에서는 `PYTHONPATH`를 `$env:PYTHONPATH = '../..;../voice'`로 설정한다.

| 경로 | 호출자 | 처리 |
|---|---|---|
| `POST /v1/pairs/invite`, `/claim`, `/unpair` | 두 역할 | N-01 가족 연결 코드 발급·입력·해제 |
| `POST /v1/calls` | 두 역할 | 통화 생성(body `assisted_role` 선택, 기본 PARENT), 첫 도움의 `trace_id` 발급, 상대에게 `call.invite` |
| `POST /v1/calls/{call_id}/accept` | 발신자의 상대 | 수락. B는 양쪽 참가 확인 뒤 `call.connected`를 보낸다 |
| `POST /v1/calls/{call_id}/rtc-token` | 페어 참가자 | B가 발급한 LiveKit 토큰 |
| `GET /v1/help/current` | 페어 참가자 | N-13 재접속 조회 |
| `WS /v1/events` | 페어 참가자 | 앱 이벤트 수신, N-11 효과·`help.ended`·`help.view` 송신 |
| `POST /v1/internal/calls/{call_id}/events` | 서버 내부(B-02·C·N-03, B의 새 코드 승인 `help.link_approved`) | 서버 이벤트 적용 (`X-Service-Token`) |
| `POST /v1/internal/calls/{call_id}/arrivals` | 서버 내부(C의 N-18) | 도착 추정 → N-22 (`X-Service-Token`) |

앱은 B가 발급한 기기 토큰을 `Authorization: Bearer <token>`으로 보낸다. WS 메시지 형식은 [계약 추가 제안 7장](../../docs/work/D-role-view-contract.md).

### B 음성 서비스 연결 (#10)

기본값 `HYOJASON_VOICE=service`는 B의 `hyojason_voice.service`(PR #24)를 사용한다. 격리 개발 환경에서는 `HYOJASON_VOICE=mock`과 `.env.example`의 테스트 토큰을 명시할 수 있다.

- 통화 ID는 B가 만든다. B가 `call.invite`를 보내면 D가 같은 `call_id`·`trace_id`로 도움 세션을 등록하고(`SessionHub.register_call`), 이후 `call.accepted/connected/ended`·`speech.final`을 같은 통화에 적용한다(`on_voice_event`).
- 동의는 `POST /v1/consent`(B N-02)로 받고 통화 후 도움에도 반영한다. `help.stop`은 B 분석 동의까지 철회한다. 가족 연결 해제는 남아 있는 도움을 종료하고 두 기기의 WS를 닫는다.
- 양쪽 LiveKit 참가 시 `call.connected`를 받는다. 모의 참가·발화 경로(`/v1/dev/...`)는 격리 개발 환경에서 `HYOJASON_ENABLE_VOICE_DEV=1`로 켠다.
- B 경로(페어·동의·통화)의 인증 오류는 B 앱이 쓰는 `403 {"detail": "UNAUTHORIZED"}`다.

### B-05(C 판단) 연결

`HYOJASON_AGENT`가 비어 있으면 Zoro URL·리비전 설정을 사용해 실제 모델을 연결한다. 둘 다 없으면 판단 없이 실행하고, 일부만 입력하면 시작할 때 누락을 알린다. C 코드(`server/agent`)를 import하므로 저장소 루트를 `PYTHONPATH`에 넣는다.

```bash
cd server/session
PYTHONPATH=../..:../voice HYOJASON_AGENT=fixture python -m uvicorn hyojason_session.api.app:create_app_from_env --factory --port 8000
```

| 값 | 동작 |
|---|---|
| `off` | C 판단 없이 앱·내부 이벤트만 처리 |
| `fixture` | C의 `B05Workflow`를 그대로 쓰고 Zoro-1만 **MOCK**(질문의 첫 선택지를 0.95로 고름). 결과는 FIXTURE |
| `zoro` | L40S의 실제 Zoro-1 (#23). `ZORO_DECIDE_URL`·`ZORO_MODEL_REVISION` 필수, 서빙 프록시가 요구하면 `ZORO_DECIDE_TOKEN`. 값이 없으면 서버가 뜨지 않는다 ([server/agent](../agent/README.md)) |

B 서비스는 RTZR 확정 발화를 D로 전달한다. 격리 시험에서는 `POST /v1/internal/calls/{call_id}/events`에도 합성 발화를 보낼 수 있다.
그러면 N-11 → C → N-11 순서로 지시(`instruction.accepted`)·목표(`goal.updated`)·대상 위치(`target.located`)가 부모 WS로 간다.
화면 관찰 뒤에는 `action.assessed`·`recovery.ready`·`guide.proposed`가 부모에게 가고, 도착 추정은 N-22를 거쳐 자녀에게 `arrival.suspected`로 간다.
결과 이벤트는 지원 대상 기기에만 간다. 흐름과 규칙은 [결정 메모 12](../../docs/work/D-status.md), 코드는 `hyojason_session/api/agent_bridge.py`.

실제 모델 확인 (접속 값은 C에게 받아 실행 환경에만 둔다):

```bash
cd server/session
HYOJASON_LIVE_ZORO=1 ZORO_DECIDE_URL=... ZORO_MODEL_REVISION=... python -m pytest tests/test_b05_zoro.py -rs
```

접속 값이 없으면 실제 모델 테스트 2개는 건너뛴다. 나머지는 로컬 가짜 `/v1/decide` 서버로 C의 HTTP 호출 경로, 접속 실패·마감 초과 때 위치 근거를 보내지 않는 동작을 확인한다.

## 구조

| 경로 | 내용 |
|---|---|
| `hyojason_session/common/` | 공통 계약: NodeResult·Diagnostic·Basis, 상태 자료형, 열거형, JSON 변환·스키마 생성 |
| `hyojason_session/help_state.py` | **N-11** 통화·도움·동의·화면 상태와 권위 버전 |
| `hyojason_session/navigation.py` | **N-12** 관찰 전이, 공백, 지시·위치·수행 이력 |
| `hyojason_session/control.py` | 통화별 쓰기 경로와 N-13의 `HelpStateReader` 구현. `submit_command`(완료·중단)·`submit_arrival`(도착)이 N-22 → N-11 → N-23 순서로 처리 |
| `hyojason_session/role_view.py` | **N-21** project_role_view |
| `hyojason_session/reconnect.py` | **N-13** reconcile_reconnect |
| `hyojason_session/completion.py` | **N-22** resolve_arrival_or_completion + 도착·종료 장부(CompletionLedger) |
| `hyojason_session/purge.py` | **N-23** purge_help_and_restart + 종료 기록(TombstoneRegistry) |
| `hyojason_session/session_state.py` | N-11 없이 N-22·N-23을 시험하는 격리용 읽기 어댑터 |
| `hyojason_session/demo_fixtures.py` | **N-24** AVD·앱·시나리오 준비 점검 |
| `hyojason_session/demo_evaluation.py` | **N-25** V-01~V-07 판정과 V-08 모델·비용 확인 |
| `hyojason_session/demo_verify.py` | N-24·N-25 JSON 실행 도구 ([예시와 리허설 절차](../../demo/fixtures/README.md)) |
| `hyojason_session/api/` | **FastAPI 제어 서버**: `app.py`(경로), `hub.py`(통화·연결·송신), `wire.py`(봉투·권한·오류 코드), `catalog.py`(화면 프로필·목표 슬롯·B-05 결과 근거), `agent_bridge.py`(B-05 연결: D → C → D) |
| `hyojason_session/views.py` | RoleSnapshot·ParentView·ChildView ([제안서](../../docs/work/D-role-view-contract.md)) |
| `hyojason_session/client_resume_gate.py` | N-13 클라이언트 규칙 참조 구현 (Android Kotlin 이식용, 서버에서 실행 안 함) |
| `hyojason_session/client_cleanup.py` | N-23 클라이언트 규칙 참조 구현 (부모 로컬 우선 정리, 완료 문구 1회) |
| `hyojason_session/ports.py` | 다른 노드 경계 (PairDirectory, HelpStateReader, HelpScopedStore, EventSink) |
| `hyojason_session/mocks/` | **MOCK**: B-01 인증 토큰, N-01~N-03 페어·통화, 격리 테스트용 N-11 상태, N-12·B-05·B-02 저장소, WS 제어 채널 |
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

노드 함수는 프레임워크와 무관하게 호출할 수 있고, 제어 서버(`api/`)는 이 함수들을 FastAPI 경로에 연결한 것이다.

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
