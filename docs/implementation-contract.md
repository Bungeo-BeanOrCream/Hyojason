# 초기 연동 순서와 모듈 경계

[공통 계약](contracts.md)의 의미는 고정하되 클래스명, 라이브러리 버전, 경로는 구현자가 결정하는 초안이다. 첫 통합에서는 한 저장소의 Android 모듈과 서버 모듈에서 시작하고, GPU 런타임은 독립 프로세스로 둔다.

## 모듈별 작업 경계

| 제안 경로 | 책임 | 데모용 대체 입력 |
|---|---|---|
| android/rtc 및 android/child | 두 역할 통화 화면, 권한 UI, 자녀 화면 | RTC가 완성되기 전에도 화면 이벤트 계약 스텁으로 사용자 상태 테스트 |
| android/observer 및 android/overlay | 프레임 처리, 화면 레지스트리, 조작 결과 단서, 부모 로컬 좌표·모드별 3초·표시 | 지원 화면 단서 fixture 또는 개발 메뉴 타깃. 최종 시연 판정에는 실화면 필요 |
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
| WS /v1/events | 인증된 pair 참가자 | 서버 지시/목표, 역할별 완료/도착과 부모 화면 메타 이벤트. 역할별 발행어 검사 |

CONFLICT는 이전 goal_revision/이미 열린 call 등을 구분해 반환한다. 권한 위반은 FORBIDDEN, 잘못된 스키마는 INVALID_EVENT, 상태가 없으면 NOT_FOUND로 처리한다. 연결 상태는 서버가 승인한다. 네트워크 공백이 생기면 로컬의 안전한 정리부터 수행하고 서버는 재접속에 최신 상태만 내보낸다.

## 상태 전이 예시

| 지점 | 인증 입력 | 서버 상태 | 화면·부수 효과 |
|---|---|---|---|
| t0 | 두 역할 통화 연결 | call ACTIVE, mode CALL_CHILD_LED, 지시 없음 | 듣는 중. 표시 0 |
| t1 | 자녀 “디스플레이 눌러 줘” | instruction_revision=1, 대상 위치 탐색 | 부모는 최초 수신 시 3초 예약 |
| t2 | 3초 전 정답 전이 | N-28 CORRECT | 테두리 0개, 새 지시 대기 |
| t3 | 새 자녀 지시 및 명시 목표 | instruction_revision=2, 새 목표가 다르면 goal_revision 증가 | 새 3초, 구 표시·제안 즉시 정리 |
| t4 | 부모가 다른 메뉴 조작 | WRONG 및 같은 epoch의 검증된 역엣지 | 마감 뒤 “뒤로 가기” |
| t5 | 확정 hangup | mode_revision 증가; POST_CALL_AGENT; 같은 help_id | RTC/STT 중지, 이전 지시 타이머 취소, 안정 화면에서 새 3초 |
| t6 | 목표 충족 근거 | ARRIVAL_PENDING | 자녀 도착 추정. 인식 지시·진행 없음 |
| t7 | 자녀 명시 완료 | help ENDED | 부모 완료 문구, 모든 도움 기록 제거 |

부모 발화, 레이아웃 위치 변동, 중복을 새 지시로 처리하지 않는다. 앱이 관측한 의미 전이와 마감 판정은 부모 단말의 동일 단조 시계를 쓴다.

## 필수 합의와 순서

1. A와 D는 screen_kind, 의미 전이의 정의, target_id 안정성·coordinate의 부모 로컬 소유권을 합의한다.
2. B와 D는 음성 토큰이 종료돼도 제어 채널이 계속 열리는지와 화면 데이터가 자녀에게 가지 않는지 확인한다.
3. C와 D는 실제 Zoro 질문 종류와 후보 ID, 보류 및 버전 취소의 사례를 정한다.
4. 네 역할이 https/로컬 주소·권한·AVD 버전을 맞춘 뒤 [시험표](scenarios.md#검증-표본과-티켓)의 A/B부터 통합한다.
