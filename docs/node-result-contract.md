# 공통 NodeResult·Diagnostic·상태 공유 계약 v1

상태는 **문서 계약 확정, 동작 검증 전**이다. 모든 Small Node는 동일한 결과 봉투를 쓰고 Big Node는 상태에 커밋할 결과와 다음 호출을 선택한다. 전송 메시지와 화면 표시 규칙은 [공통 전송 계약](contracts.md)을 따른다.

## 1. 반환 형식

| 필드 | 타입 | 규칙 |
|---|---|---|
| status | success, partial, error | 노드가 필수 처리를 수행했는지 나타낸다 |
| outcome | 노드에 등록된 snake_case 코드 또는 null | 업무상 결과다. 정상적으로 대기한 경우에도 코드가 있다. error에서는 null |
| data | 노드별 T 또는 null | success·partial은 해당 노드 타입을 반환한다. 빈 배열이나 값의 부재도 유효한 형식으로 표현한다. error는 null |
| diagnostics | Diagnostic | 실행, 보류, 실패, 입력 결과 참조를 어떻게 해석할지 제공한다. 반환 필드 네 개 모두 항상 존재한다 |

| 반환 상태 | 발생 조건 | 업무 결과와 데이터 | 호출자 처리 |
|---|---|---|---|
| success | 해당 호출에서 실행해야 할 검증·처리가 정상적으로 끝남 | 유효 T와 일반 결과 또는 no_candidates, waiting, closed 등의 코드 | 업무 결과와 버전을 보고 다음 엣지를 실행한다 |
| partial | 일부 입력 또는 경로가 실패했으나 독립적인 일부 데이터는 검증됨 | 유효 T와 부분 범위를 나타내는 코드. 빈 후보도 가능 | 명시적으로 허용된 부분 결과만 전달한다. 0건을 전수 검색의 0건으로 취급하지 않는다 |
| error | 소속·동의·형식·버전 검증 실패 또는 안전한 결과를 낼 필수 단계 실패 | outcome=null, data=null. diagnostic에 실패 위치·코드 | 부수 효과 차단, 안전한 상태로 복귀. 기존 최신 상태를 무효 데이터로 덮지 않는다 |

호출 전 조건이 성립하지 않으면 실행 결과를 만들지 않고 상위 Diagnostic에 skipped를 기록한다. 예를 들어 화면 후보가 0건이면 N-17을 호출하지 않는다. 한 번 실행한 뒤 보류한 경우에는 success와 해당 업무 결과가 남는다.

~~~mermaid
flowchart LR
  envelope["NodeResult"]
  envelope --> execution["status · 실행 상태"]
  envelope --> outcome["outcome · 업무상 결과"]
  envelope --> payload["data · 검증된 산출물"]
  envelope --> diagnostic["diagnostics · 근거와 실행 기록"]
  diagnostic --> basis["입력 버전과 result 참조"]
  diagnostic --> steps["단계별 실행·실패·생략"]
  diagnostic --> origin["LIVE 또는 FIXTURE"]
~~~

## 2. Diagnostic과 오류 코드

| 필드 | 타입·필수 여부 | 제약 |
|---|---|---|
| trace_id | ID · 필수 | 최초 이벤트에서 생성하고 다시 판단할 때 동일 인과 사슬로 추적 가능하게 보존 |
| run_id | ID · 필수 | 노드 호출마다 유일. 추가 호출에는 새 run_id |
| node_id | ID · 필수 | B-xx 또는 N-xx. 실행/생략 단계 이름과 분리 |
| source | LIVE 또는 FIXTURE · 필수 | 실제 연결, 모델·기기 관측과 시험 주입을 구분. 하위 입력이 FIXTURE이면 파생 실행에도 FIXTURE를 이어받음. 실제 모델 실행 자체는 N-14 결과의 provenance로 별도 확인 |
| basis | Basis · 필수 | 실행 시작에 잠근 적용 조건. 결과 적용 직전에 관련 버전의 최신성을 다시 확인 |
| input_refs | ResultRef[] · 필수, 빈 배열 가능 | 직접 읽은 이전 결과를 참조. 원문 복사 금지 |
| duration_ms | 비음수 정수 · 필수 | 실행을 재는 프로세스의 단조 시계 차이. 다른 프로세스 시계와 빼지 않음 |
| steps | 단계명 → StepDiagnostic · 필수 | 각 노드의 내부 처리 단계에 실행 상태와 이유를 남긴다 |
| terminal_error | null 또는 ErrorDescriptor · 필수 | error에만 값이 있어야 한다. partial에 걸린 단계 실패는 steps에만 기록 |

StepDiagnostic의 status는 success / failed / skipped다. code는 짧은 고정 코드 또는 null이다. 실패와 생략에는 code가 필요하다. 선택적으로 duration_ms, count, retryable을 기록한다. 사용자 원문, 스택 트레이스, 화면 좌표, 토큰, 사적인 계정은 여기에 넣지 않는다. ErrorDescriptor는 code, step, retryable 세 필드를 가진다. retryable은 조건을 다시 확인할 수 있다는 힌트다. 노드 내부 재귀나 즉시 무한 재호출을 뜻하지 않는다.

| 공통 코드 | 의미 | 처리 |
|---|---|---|
| INVALID_INPUT / UNAUTHORIZED | 스키마 위반, 인증·역할·페어 불일치 | 무효 데이터 폐기. 권한이나 범위를 완화하지 않는다 |
| STALE_BASIS | 대상 도움, 동의, 화면, 목표 또는 발화 버전 불일치 | 해당 시도의 재시도 금지. 최신 입력이 별도 트리거가 됨 |
| DEPENDENCY_UNAVAILABLE / TIMEOUT | 전송·STT·모델 등 의존 요소 불가 또는 시간 제한 초과 | 부수 효과 없음. 예약된 새 유효 이벤트에서 다시 평가 |
| NO_CONSENT / CAPTURE_GAP / PROTECTED_SCREEN | 동의 없음, 관찰 공백, OS 보호 화면 | 표시 제거 및 범위 제한. 정상 대기인지 필수 처리 실패인지는 해당 노드 표를 따른다 |
| LOW_CONFIDENCE / NO_CANDIDATES / SEARCH_INCOMPLETE | 후보 해석 불명확, 정상 탐색 0건, 탐색 일부 누락 | 세 경우를 따로 기록. 보류 또는 검증된 현재 화면 후보만 사용 |
| OVERLAY_BLOCKED / TOUCH_BLOCKED | 화면에 표시 불가 또는 실제 사용자 터치 통과 실패 | 원을 제거. 단계 완료로 집계하지 않음 |
| DUPLICATE / ALREADY_ENDED / NOT_APPLICABLE | 중복, 종료된 도움 또는 실행 조건 미충족 | 새로운 부수 효과를 만들지 않고 상위가 기존 상태 확인 |

업무 결과 코드는 각 노드에 등록한다. 같은 원인이라도 단계 code와 업무 outcome이 같은 문자열일 필요는 없다.

## 3. 상태의 쓰기 책임과 이전 노드에 알리는 방법

| 상태 | 소유자 | 읽는 쪽과 전달 방식 |
|---|---|---|
| Pair/Call/Consent | B-01 및 제어 서버 | 서버의 최신 승인 snapshot으로 B-02·B-04가 읽는다 |
| Instruction/Goal/Help/NavigationTrail | N-11·N-12가 제어 서버의 도움별 상태에 커밋 | B-05가 읽기 전용으로 받는다. N-15는 지시와 목표 후보를 반환하고 N-11이 각각 revision을 발급한다 |
| ScreenState | N-09/N-10이 부모에서 관찰, N-11이 서버 요약 수락 | N-16~18이 서버 요약을 읽는다. 원본과 좌표는 부모 엔진에만 남긴다 |
| TargetDecision/ActionVerdict/GuideProposal/ArrivalDecision | N-27/N-28/N-17/N-18이 제안 | B-06/B-07이 최신 Basis로 검사한 뒤 실행. 제안 결과를 되돌려서 관찰 결과로 만들지 않는다 |
| 표시·종료 확인 | B-06/B-07에서 관측 | guide.shown/suppressed, help.ended를 N-11에 받은 뒤 다음 N-16/18에 반영한다 |

Basis의 필수 키는 pair_id, call_id, help_id, event_seq, consent_revision, goal_revision, mode_revision, instruction_revision, projection_epoch, screen_revision, utterance_seq다. 해당 단계에서 생성 전이거나 관여하지 않으면 null로 표시한다. 숫자는 0 이상의 정수다. 모든 표시 및 도착 제안은 도움·동의·모드·화면·캡처 버전이 전부 존재해야 한다. 통화 중에는 instruction_revision, 통화 후에는 goal_revision이 필수다. N-15의 지시 판단은 최신 자녀 utterance_seq, 목표 확률 판단은 최신 확정 utterance_seq(두 역할)와 일치해야 한다. N-28은 증거 화면 쌍을 검사하고 N-19는 로컬 레이아웃과 조작 보류 상태를 추가로 본다. 결과에 참여하지 않은 버전이 달라졌다는 이유로 무관한 계산을 폐기하지 않는다.

ResultRef의 필수 키는 trace_id, node_id, run_id다. 대상 결과의 Basis는 참조를 읽는 쪽이 다시 검사한다. 결과는 도움별 제한된 메모리 상태에 두고 보존 기간은 **도움 종료까지**다. 프레임·오디오·좌표·발화 원문은 이 상태 기록에 저장하지 않는다. 승인한 지시의 행동·target_id와 화면 revision은 기록하며 실제 픽셀 좌표는 로컬에서만 읽는다. 자녀 화면에는 역할에 허용된 요약만 전달한다. 종료에 필요한 멱등 식별자는 사용자 내용 없이 짧게 유지할 수 있다. 이전 노드는 자신의 과거 결과를 수정하지 않는다. 후속 결과에서 재관찰이 필요하면 새 관찰 이벤트를 보내고, 해당 버전의 새 노드 실행에서 읽는다.

~~~mermaid
flowchart TD
  evt["새 이벤트"] --> reducer["N-11 도움별 상태 갱신"]
  reducer --> snapshot["읽기 전용 상태와 Basis"]
  snapshot --> compute["N-15~18 또는 N-27~28 계산"]
  compute --> commit{"현재 버전과 일치?"}
  commit -->|"예"| consumer["N-19 또는 N-22 재검증"]
  commit -->|"아니요"| old["STALE_BASIS 기록"]
  consumer --> feedback["표시 · 보류 · 도착 결과"]
  feedback --> evt
~~~

## 4. 부분 결과 예시

N-16의 현재 화면 쪽 탐색은 정상적으로 끝났으나 후보가 0개이고, 방문 경로 탐색은 공백으로 끝내지 못한 상황이다. 완료한 전수 탐색의 빈 결과는 success/no_candidates다.

~~~json
{
  "status": "partial",
  "outcome": "search_incomplete",
  "data": {
    "candidates": [],
    "coverage": "partial",
    "active_count": 0,
    "recovery_count": 0
  },
  "diagnostics": {
    "trace_id": "trace-01",
    "run_id": "run-16-a",
    "node_id": "N-16",
    "source": "FIXTURE",
    "basis": {
      "pair_id": "pair-fixture",
      "call_id": "call-fixture",
      "help_id": "help-fixture",
      "event_seq": 19,
      "consent_revision": 1,
      "goal_revision": 2,
      "mode_revision": 2,
      "instruction_revision": null,
      "projection_epoch": "epoch-fixture",
      "screen_revision": 5,
      "utterance_seq": 3
    },
    "input_refs": [
      {"trace_id": "trace-01", "node_id": "N-12", "run_id": "run-12-a"}
    ],
    "duration_ms": 4,
    "steps": {
      "visible_scan": {"status": "success", "code": null, "count": 0},
      "recovery_scan": {"status": "failed", "code": "CAPTURE_GAP", "retryable": false}
    },
    "terminal_error": null
  }
}
~~~

호출자는 N-17을 생략하고 재관찰을 기다린다. 이 결과에서 실제 버튼이 존재하지 않는다거나 목표에 도달했다고 판단하지 않는다.
