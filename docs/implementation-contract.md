# 초기 연동 순서와 모듈 경계

[공통 계약](contracts.md)의 의미는 고정하되 클래스명, 라이브러리 버전, 경로는 구현자가 결정하는 초안이다. 첫 통합에서는 한 저장소의 Android 모듈과 서버 모듈에서 시작하고, GPU 런타임은 독립 프로세스로 둔다.

## 모듈별 작업 경계

| 제안 경로 | 책임 | 데모용 대체 입력 |
|---|---|---|
| android/rtc 및 android/child | 두 역할 통화 화면, 권한 UI, 자녀 화면 | RTC가 완성되기 전에도 화면 이벤트 계약 스텁으로 사용자 상태 테스트 |
| android/observer 및 android/overlay | 프레임 처리, 화면 레지스트리, 부모 로컬 좌표·3초·표시 | 지원 화면 단서 fixture 또는 개발 메뉴 타깃. 최종 시연 판정에는 실화면 필요 |
| server/session | 페어·초대·제어 채널, 도움 상태, 연결·정리·최종 완료 | 합성 speech.final과 screen.observed로 reducer 통합 시험 |
| server/agent | 선택지 구성·상태 조건·Zoro 추론 adapter | 상태 머신 테스트에서는 고정 응답을 허용. 결과에 simulated 표시 |
| demo/fixtures | 앱별 샘플 화면의 비민감 ID·기대값, 합성 발화, 지연 시험 입력 | 환자나 가족의 실제 미디어를 테스트 자산으로 사용하지 않음 |

공개 저장소의 제어 주소, .env.example, 로그에는 실제 시연 계정/토큰을 넣지 않는다. dev 모드 스텁과 라이브 경로는 출력의 source로 구별한다.

## 제어 엔드포인트 제안

| 메서드와 경로 | 호출 권한 | 처리 |
|---|---|---|
| POST /v1/pairs/claim | 로그인한 두 역할 | 일회용 페어 승인. 응답은 승인 상태만 |
| POST /v1/calls | 인증된 부모 | 허용 pair의 새 call_id. 자녀에게 수신 이벤트 |
| POST /v1/calls/{call_id}/accept | 해당 자녀 | 수락 및 각 역할의 단기 RTC 토큰 발급 경로 활성 |
| POST /v1/calls/{call_id}/rtc-token | pair 참가자 | 승인된 call/역할에만 토큰 발급. 실음성 룸 접속 |
| GET /v1/help/current | 해당 pair 참가자 | 역할별 현재 상태. 연결 재개 시 명령 전체를 받지 않음 |
| WS /v1/events | 인증된 pair 참가자 | 서버 명령·목표·완료와 부모 화면 메타 이벤트. 역할별 발행어 검사 |

CONFLICT는 이전 goal_revision/이미 열린 call 등을 구분해 반환한다. 권한 위반은 FORBIDDEN, 잘못된 스키마는 INVALID_EVENT, 상태가 없으면 NOT_FOUND로 처리한다. 연결 상태는 서버가 승인한다. 네트워크 공백이 생기면 로컬의 안전한 정리부터 수행하고 서버는 재접속에 최신 상태만 내보낸다.

## 상태 전이 예시

| 시각 | 입력 | 서버 상태 | 부모 표시와 자녀 상태 |
|---|---|---|---|
| t0 | 실제 통화 연결 | call ACTIVE, help LISTENING | 파악 중, 원 없음 |
| t1 | 말 끝에서 FONT 확정 | goal_revision=1 | 양쪽 FONT, 로컬 3초 예약 |
| t2 | Settings 화면 전환 | screen_revision=7 | 이전 표시 제거, 현재 대상 선택 |
| t3 | timeout으로 정정 | goal_revision=2 | 이전 테두리 제거, 표시 시각 재예약 |
| t4 | 이전 FONT 모델 응답 | 버전 불일치 | 폐기. 자녀 목표는 TIMEOUT 유지 |
| t5 | 올바른 10분 상태 관찰 | ARRIVAL_PENDING | 부모의 다음 가이드 없음, 자녀 추정 알림 |
| t6 | 자녀 완료 명령 | help ENDED | 앱 UI 갱신 및 도움 이력 정리 |
| t7 | 통화가 살아 있고 새 알람 목표 | 새 help_id | 이전 목표와 좌표 없이 재시작 |

3초 마감 중 핑이나 디버그 로그가 초과하면 과거 목표나 좌표를 어떻게든 표시하는 정책을 만들지 않는다. 동일 화면의 로컬 최신성이 확인된 경우에만 뒤늦은 표시를 따로 기록한다.

## 필수 합의와 순서

1. A와 D는 screen_kind, 의미 전이의 정의, target_id 안정성·coordinate의 부모 로컬 소유권을 합의한다.
2. B와 D는 음성 토큰이 종료돼도 제어 채널이 계속 열리는지와 화면 데이터가 자녀에게 가지 않는지 확인한다.
3. C와 D는 실제 Zoro 질문 종류와 후보 ID, 보류 및 버전 취소의 사례를 정한다.
4. 네 역할이 https/로컬 주소·권한·AVD 버전을 맞춘 뒤 [시험표](scenarios.md#검증-표본과-티켓)의 A/B부터 통합한다.
