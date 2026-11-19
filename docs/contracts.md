# 공통 계약

상위 구간은 [전체 구조](architecture.md), 요구사항별 구현·시험 담당은 [작업 배분](work-plan.md)을 참고한다. 아래는 P0 구현과 통합 테스트가 맞춰야 할 v1 계약이다.

## ID와 버전

| 필드 | 생성 위치 | 규칙 |
|---|---|---|
| pair_id | 제어 서버 | 승인된 부모 1명과 자녀 1명의 관계. 사용자에게 내부 ID를 보이지 않음 |
| call_id | 제어 서버 | 통화 요청마다 새 ID. 내려받는 RTC 토큰은 해당 방과 역할에만 사용 |
| help_id | 제어 서버 | 동의 유효한 최초 연결에서 LISTENING 생성. 한 통화에서 완료 후 다른 목표는 새 도움 ID 사용 |
| utterance_id / utterance_seq | 발화 처리 | 부분 발화는 ID가 같고 확정만 제어 서버 처리. seq가 이미 적용된 확정 발화보다 작으면 폐기 |
| goal_revision | 제어 서버 | 도움별 0으로 시작. 목표 확정·변경·슬롯 변경이 채택될 때 증가. 단순 모델 재호출에서는 유지 |
| projection_epoch | 부모 AVD | MediaProjection 재승인으로 새로 시작할 때 새 값. 이전 캡처의 프레임과 결과 폐기 |
| screen_revision | 부모 AVD | 의미 있는 화면 전이·재관찰로 증가. 같은 화면의 마이크로 프레임마다 증가시키지 않음 |
| layout_revision | 부모 AVD | 크기·회전·스크롤·IME 등의 좌표 변화. 해당 위치를 새로 확인하기 전 표시 제거 |
| decision_id | 제어 서버 | 요청과 모델 결과 추적. 동일 ID 수신을 중복 렌더하지 않음 |
| event_id / event_seq | 발행자와 서버 | event_id는 재전송 중복 제거. event_seq는 서버의 도움별 수신 순서. 다른 장치의 시계를 비교해 이벤트를 정렬하지 않음 |

좌표용 프레임 시각과 3초 타이머는 부모 단말의 monotonic clock에서 비교한다. 음성 끝 → 목표 표시 지연은 테스트 영상의 공통 시간축 또는 동일한 측정기에서 계산한다. 모의 로그의 벽시계는 지연 기준으로 사용하지 않는다.

## 전송과 권한

- WebSocket 제어 채널은 음성 RTC와 독립적으로 유지한다. 세션 서버에 대한 REST는 토큰·초대·상태 조회와 재접속 시 요약 조회에 사용한다. 네트워크 전송은 TLS를 사용한다. 로컬 개발 예외는 디버그 빌드에서만 허용한다.
- 서버는 단말의 인증과 pair_id 소속, 역할, 호출/도움의 연결, 허용 상태를 다시 검사한다. 자녀는 screen.observe를 발행할 수 없고 부모는 help.complete를 발행할 수 없다. 도움 완료는 등록된 자녀, 안내 끄기는 등록된 부모만 보낼 수 있다.
- 하트비트는 접속을 확인하는 용도다. 화면 최신성을 대신하지 않는다. 중복 완료, 늦게 도착한 목표, 이미 종료된 도움은 기존 응답을 재사용하거나 폐기한다.
- 재접속은 최신 snapshot을 요청하고 과거 Overlay 명령을 재생하지 않는다. 화면을 다시 관찰한 시점에 새 선택을 요청한다. 라이브 데모 P0에서 다중 서버·분산 세션 복구는 범위에 포함하지 않는다.

## 이벤트와 예시

모든 제어 이벤트는 v, type, event_id, pair_id, call_id, help_id를 사용한다. call_id/help_id는 생성 이전 이벤트에서만 null이 가능하다. actor는 인증으로 서버가 확정하며 클라이언트 값만 신뢰하지 않는다. payload는 이벤트별 타입이다.

~~~json
{
  "v": 1,
  "type": "guide.proposed",
  "event_id": "e-031",
  "pair_id": "pair-demo",
  "call_id": "call-demo",
  "help_id": "help-02",
  "payload": {
    "decision_id": "zoro-42",
    "goal_revision": 2,
    "projection_epoch": "capture-1",
    "screen_revision": 8,
    "choice_id": "tap.settings.screen_timeout",
    "action": "TAP",
    "target_id": "settings.screen_timeout",
    "confidence": 0.91,
    "valid_for_ms": 4500,
    "message_key": "tap_visible_item"
  }
}
~~~

| 이벤트 | 보내는 쪽 → 받는 쪽 | payload의 최소 필드와 효과 |
|---|---|---|
| call.invite / accepted / ended | 인증된 역할 ↔ 서버 ↔ 상대 | 역할과 call_id, 종료 사유. accepted와 RTC 연결·동의를 확인한 뒤 STT 시작. ended는 STT만 닫을 수 있음 |
| consent.updated | 각 단말 → 서버 | consent_revision, purpose, enabled. 캡처 가능 여부와 모델 처리 동의를 별도 상태로 유지 |
| speech.final | STT → 서버 | utterance_id, seq, speaker_role, text, ended_at, source. text는 세션 처리 한정, 디버그 시험 입력은 source로 구분 |
| screen.observed | 부모 → 서버 | projection_epoch, screen_revision, screen_kind, target_ids, verified_signals, observation_health. 원본·범용 OCR 텍스트·좌표는 제외 |
| goal.updated | 서버 → 양쪽 | goal_id, goal_revision, 필요한 표시용 슬롯, source_utterance. 두 역할이 최신 revision 표시 |
| guide.proposed / cleared | 서버 → 부모 | 위 제안 또는 폐기 이유. 부모가 로컬 최신 상태로 최종 검사 |
| guide.shown / suppressed | 부모 → 서버 | decision_id, 현재 revision, elapsed_ms, 가려진 이유. 품질 로그에 원문 제외 |
| arrival.suspected / revoked | 서버 → 자녀 | goal_revision, screen_revision, arrival_id, 요약된 근거 코드. 자동 완료하지 않음 |
| help.complete / stop | 자녀 / 부모 → 서버 | help_id, 최신 goal_revision, 명시적 사유. 잘못된 버전이거나 종료된 ID면 새 도움을 건드리지 않음 |
| help.ended | 서버 → 양쪽 | 사유와 종료 이벤트. 캐시·화면 안내 정리. COMPLETE이면 부모 화면에 완료 문구 |

### 상태와 로그

~~~text
NodeResult<T>
  status = success | partial | error
  outcome = 업무 결과 코드
  data = T 또는 null
  diagnostics = {node_id, trace_id, latency_ms, error_code?, retryable?}
~~~

success / no_goal, success / no_target, success / not_arrived는 정상적인 업무 결과다. 모델 timeout이나 연결 실패를 확신 낮음으로 바꿔 기록하지 않는다. partial은 일부 입력이 실패했어도 검증된 상태를 제공할 때만 사용한다. 권한·버전 경계 실패는 실행 중단 또는 안전한 재관찰로 연결한다. 로그에는 역할, 버전, 결과 코드, 지연, revision 등만 남긴다. 확정 발화 텍스트도 영속 로그에 포함하지 않는다.

## 화면·선택지·시간 기본값

| 계약 | 내용 |
|---|---|
| ScreenState | 후보는 안정적인 target_id와 사전에 승인한 label_key를 사용. 화면의 본문·개인 정보·모델 지시로 오해할 만한 문자열을 상태 값으로 보내지 않음 |
| Candidate | choice_id, action, target_id 또는 null, label_key, 현재 화면 증거. 사용자 입력에 필요한 값은 승인된 목표 슬롯에서만 가져오고 운영 로그에는 기록하지 않음 |
| GuideAction | TAP, BACK, SCROLL_DOWN, SCROLL_UP, INPUT_HINT, WAIT, ASK_CHILD. 홈 이동 요청은 시스템 홈 조작을 설명하는 TAP 유형의 텍스트 전용 후보로 제한 |
| 금지 | 자동 클릭·입력·실행 없음. 삭제·공장 초기화·계정 제거·OTP/비밀번호 전달·모르는 URL·모르는 수신자에는 가이드 후보를 만들지 않음. 정책은 모델 호출 전후 모두 적용 |
| 인식 안정화 | 로컬 처리 초기값 초당 2–4 프레임, 같은 의미 화면이 2회 확인된 뒤 확정. 지연과 발열 실측 후 조정 |
| 3초 안내 | 목표 확정/변경 또는 의미 있는 화면 전이의 부모 단말 관측부터 3,000ms가 지나면 표시 가능. 기존 강조는 즉시 제거. 영상으로 검증하는 PRD 수용 범위는 3초 ±1초 |
| 유효성 | 제안 수신 시부터 valid_for_ms만큼 살되 goal_revision 및 화면·캡처 버전이 바뀌면 즉시 폐기. 마지막 로컬 프레임이 1초 넘게 오래됐으면 재관찰 후 적용 |
| 모델 확신 | 시작값 예시 top ≥0.70, top–second ≥0.15. 20개 대화 검증으로 조정. 확률 보정을 검증한 값이 아니며 단일 판단·리허설 결과와 함께 기록 |
| 모델 호출 | 새 확정 발화, 유효 목표 생성·변경, 안정된 의미 화면/완료 근거 변경에서 호출. 픽셀마다 호출하지 않음. 독립 질문은 모델이 지원하는 경우 한 요청으로 묶고 의존하는 판단은 새 상태에서 차례로 호출 |
