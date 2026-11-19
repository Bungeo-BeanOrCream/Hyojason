현재 담당: Claude Code (2026-11-19) · D 슬롯

# 계약 추가 제안 · RoleSnapshot·ParentView·ChildView, HelpSnapshot 필드

`docs/contracts.md`에는 N-13·N-21이 내보내는 RoleSnapshot·ParentView·ChildView의 필드가 없다. D 구현(`server/session`)에서 아래처럼 정했고, 영향받는 A(부모 띠 N-20)·B(자녀 UI)가 확인하면 PR로 `docs/contracts.md` 자료형 표에 반영한다. **합의 전 초안**이다. 기계 판독용 스키마는 [`server/session/schemas/`](../../server/session/schemas/)에 있다(이슈 #2).

## 1. 역할 앱이 받는 타입

| 타입 | 필드 | 유효성 및 범위 |
|---|---|---|
| RoleSnapshot | role, pair_id, call_id?, help_id, parent_view?, child_view? | N-13 재접속 응답. 조회 역할의 view만 채우고 다른 쪽은 null. 식별자는 제어 이벤트 봉투(v, type, event_id, pair_id, call_id, help_id)를 채우는 데만 쓴다 |
| ChildView | call_state, can_complete, arrival_indicator | N-21 규칙대로 세 키만. `can_complete`는 help_state가 ACTIVE/ARRIVAL_PENDING이고 자녀 제어 채널이 연결돼 있을 때 true. `arrival_indicator`는 NONE/SUSPECTED, 통화 후(POST_CALL_AGENT·call ENDED)에만 SUSPECTED |
| ParentView | status, call_state, help_state, mode, mode_revision, goal?, instruction?, can_stop_guidance, completion_message_key? | 부모 상단 띠(N-20) 입력. 화면·target_ids·좌표·GuideProposal·발화 원문/ID·이력 없음 |
| ParentGoalView | goal_id, goal_revision, approved_slots | 검증된 현재 목표. 도움이 끝나면 null |
| ParentInstructionView | instruction_id, instruction_revision, action, target_key? | 통화 중(CALL_CHILD_LED) 현재 지시의 허용 상태(N-13 “허용 지시 상태”). 통화 후에는 null |

ParentView.status 값

| 값 | 조건 | 띠 문구 예 |
|---|---|---|
| LISTENING | 도움 활성, 지시·목표 없음 (N-21 view_waiting) | 듣고 있어요 |
| CALL_GUIDING | CALL_CHILD_LED, 지시나 목표 있음 | 통화 중 안내 |
| POST_CALL_GUIDING | POST_CALL_AGENT | 통화 후 이어서 안내 |
| ARRIVAL_PENDING | POST_CALL_AGENT, 도착 추정 알림 중 | 자녀 확인을 기다려요 |
| COMPLETED | 자녀가 완료 (end_reason=CHILD_COMPLETED) | 다 됐어요! (`completion_message_key=help.completed`) |
| STOPPED | 부모가 안내 끄기 | — |
| ENDED | 그 밖의 종료(통화 종료 시 목표 없음 등) | — |

`can_stop_guidance`는 도움이 끝나지 않았으면 항상 true다. N-23의 “네트워크 오류가 있으면 부모 끄기는 로컬에서 즉시 정리” 규칙 때문에 연결 상태와 묶지 않는다.

## 2. N-13 결과의 resume_state

| 값 | 의미 | 클라이언트 처리 |
|---|---|---|
| RESUMED | 알던 도움이 그대로 활성 | view 교체. 부모는 Overlay·타이머 제거 뒤 재관찰 |
| SWITCHED | 알던 도움은 없거나 다르고 다른 활성 도움이 있음 | 이전 도움 캐시 제거 뒤 새 view |
| ENDED | 활성 도움 없음, 알던 도움은 종료 기록 있음 | 로컬 안내·캐시 제거 |
| UNKNOWN | 활성 도움 없음, 서버가 그 도움을 모름(재시작 등) | 로컬 안내·캐시 제거. 오래된 가이드 적용 금지 |
| NO_HELP | 양쪽 모두 도움 없음 | 없음 |

부모 재관찰 장벽: `must_reobserve=true`이면 부모는 새 화면 관찰을 보내기 전까지 표시하지 않고, 재접속 전 Basis(`event_seq ≤ server_event_seq`)로 만든 제안은 재사용하지 않는다. 같은 화면이면 screen_revision이 그대로라 화면 버전만으로는 막을 수 없어서 event_seq를 장벽으로 쓴다. Kotlin 이식용 참조 구현은 [`client_resume_gate.py`](../../server/session/hyojason_session/client_resume_gate.py).

## 3. HelpSnapshot 추가 필드 (N-11 소유, D)

contracts.md의 HelpSnapshot 필드에 세 개를 더했다.

| 필드 | 이유 |
|---|---|
| pair_id | 다른 페어의 snapshot을 거절하기 위해 (N-21 21.3, N-13 13.3) |
| call_id? | 역할 앱이 제어 이벤트 봉투를 채우기 위해. 통화 전이면 null |
| end_reason? | 자녀 완료일 때만 부모 완료 문구를 내기 위해 (N-20, N-23). help_state=ENDED면 필수 |

## 4. 확인 요청

- **B**: ChildView 세 키와 `can_complete`·`arrival_indicator` 규칙이 자녀 UI에 충분한가. help_id·call_id를 RoleSnapshot 봉투에서 받는 방식이 괜찮은가.
- **A**: N-20 `banner: ParentView` 입력으로 위 status 값과 `completion_message_key`가 충분한가. 재접속 시 `client_resume_gate.py`의 규칙(Overlay·타이머 제거 → 재관찰 → 표시)을 부모 앱에 옮길 수 있는가.
- **C**: HelpSnapshot을 읽기 전용으로 받을 때 추가 필드가 문제없는가.
