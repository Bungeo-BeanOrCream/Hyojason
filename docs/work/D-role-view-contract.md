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

## 5. N-22·N-23 계약 추가

기계 판독용: `arrival-decision`, `control-event`, `n22-completion-data`, `n23-purge-data` 스키마.

| 타입·이벤트 | 필드 | 규칙 |
|---|---|---|
| ArrivalDecision (C → D) | arrival_id, arrived, goal_revision, evidence_codes[], basis | basis에 help_id·consent_revision·mode_revision·goal_revision·projection_epoch·screen_revision이 모두 있어야 한다. arrived=true면 evidence_codes가 비어 있으면 안 된다. 현재 snapshot과 하나라도 다르면 STALE_BASIS |
| help.complete / help.stop (자녀 / 부모 → D) | 봉투 + help_id | 범위는 help_id로만 확인한다(자녀는 goal_revision을 모름). 완료는 자녀만, 중단은 부모만. 지시·목표가 생기기 전(LISTENING)에는 자녀 완료 불가. 서버는 이를 N-22(`SessionControl.submit_command`)로 보내고, 승인되면 서버 내부 이벤트 `help.end`로 N-11에 반영한다. 앱은 `help.end`를 보내지 않는다 |
| arrival.suspected / revoked (D → 자녀) | 봉투 + arrival_id | 통화 후(POST_CALL_AGENT)에만. (help, mode, goal, arrival_id)로 중복 제거, 같은 목표에는 한 번 |
| help.ended (D → 양쪽) | 봉투(event_id = end_event_id) + payload {end_event_id, reason} | reason은 CHILD_COMPLETED / PARENT_STOPPED / NO_GOAL_AT_HANGUP. 부모 "다 됐어요!"는 CHILD_COMPLETED일 때 end_event_id마다 한 번. 다른 help_id의 help.ended는 무시 |
| HelpTombstone (N-23 내부) | pair_id, help_id, end_event_id, end_reason, final_event_seq, resume_requires_consent | 사용자 내용 없음. N-11은 새 도움을 열기 전에 resume_requires_consent를 확인 |

N-23이 비우는 범위(CleanupScope): 서버 HELP_STATE · NAVIGATION_TRAIL · INFERENCE_RESULTS · PENDING_UTTERANCES · ARRIVAL_NOTICE, 단말 PARENT_DEVICE(캡처·좌표·타이머·테두리·띠) · CHILD_DEVICE(완료 버튼·도착 알림). 단말 범위는 help.ended를 전달했을 때만 정리된 것으로 센다. 단말 쪽 규칙의 Kotlin 이식용 참조 구현은 [`client_cleanup.py`](../../server/session/hyojason_session/client_cleanup.py).

추가 확인 요청
- **B**: 자녀 앱이 help.complete에 help_id만 실어 보내고, help.ended·arrival.* 를 위 규칙으로 처리할 수 있는가.
- **C**: N-18 ArrivalDecision의 basis를 위 필드로 채울 수 있는가. 판단 결과 저장소가 생기면 `purge(help_id)`(scope=INFERENCE_RESULTS)를 구현해 N-23 정리에 연결해 달라.

## 6. 제어 이벤트 봉투에 trace_id 추가 (제안)

trace_id는 서버가 발급한다(D-status 결정 메모 6). 이를 앱과 주고받으려면 contracts.md의 봉투 `v, type, event_id, pair_id, call_id, help_id, payload`에 필드가 하나 더 필요하다.

| 필드 | 규칙 |
|---|---|
| trace_id | 도움 1회에 1개(D-status 결정 메모 9). 첫 도움은 서버가 통화를 만들 때, 다음 도움은 새 코드 승인(`help.link_approved`) 때 발급한다. 서버 → 앱 이벤트에는 항상 싣는다. 앱 → 서버 이벤트에는 받은 값을 그대로 다시 싣고, 앱이 새로 만들지 않는다. 다르면 서버는 통화의 값으로 기록한다. 통화 생성 전 이벤트(`call.invite` 요청)에는 null |

앱은 부모 N-09·N-10·N-19 기록, 자녀 앱 기록, 리허설 증거 파일에 같은 trace_id를 찍는다. 통화 생성 응답을 받기 전에는 지시·안내가 생기지 않으므로 그 전 기록은 판정에 쓰지 않는다.

확인 요청: A(부모 앱 기록·증거 내보내기), B(자녀 앱·통화 생성 응답 처리).

## 7. 제어 채널(WS /v1/events) 메시지 (제안)

제어 서버 뼈대(`server/session/hyojason_session/api`)에서 정한 규칙이다. 봉투는 contracts.md 7키를 그대로 쓰고, 그 밖의 키가 있으면 거절한다.

| 방향 | 메시지 | 내용 |
|---|---|---|
| 앱 → 서버 | 부모: `consent.updated`, `projection.started`, `screen.observed`, `guide.shown`, `guide.suppressed`, `help.stop` / 자녀: `consent.updated`, `help.complete` | 역할은 토큰에서 정한다. 목록 밖 이벤트는 거절. 서버는 받은 시점의 서버 Basis로 N-11에 적용하고, 앱의 `help_id`가 현재 도움과 다르면 거절(STALE_BASIS) |
| 서버 → 앱 | `event.ack` {event_id, outcome} / `event.rejected` {event_id, code, node_code} | 앱이 보낸 이벤트마다 하나. code는 FORBIDDEN·INVALID_EVENT·NOT_FOUND·CONFLICT (implementation-contract) |
| 서버 → 앱 | `help.view` {role, pair_id, call_id, help_id, parent_view, child_view} | 상태가 바뀔 때마다 N-21 역할 화면 (B-07 정책 1). 재접속은 `GET /v1/help/current` |
| 서버 → 부모 | N-11 효과: `instruction.accepted`, `instruction.cleared`, `goal.updated`, `mode.changed`, `screen.reobserve`, `guide.suspend`, `observation.stop`, `audio.evaluate` | payload에 해당 값과 `basis`(식별자·버전뿐) |
| 서버 → 자녀 | `call.invite`, `help.view`, `arrival.suspected`, `arrival.revoked`, `help.ended` | 이 다섯 가지 밖의 이벤트는 자녀에게 보내지 않는다 |
| 양방향 | `ping` {event_id} → `pong` {event_id} | 연결 확인. 앞서 보낸 메시지가 모두 나간 뒤 온다 |

REST 오류는 `{"error": {"code", "node_code"}}`이고, 토큰이 없거나 틀리면 401 UNAUTHENTICATED다.

확인 요청: A(부모 앱 수신 처리), B(자녀 앱 수신 처리·`call.invite` 뒤 `POST /v1/calls/{id}/accept`).

## 8. 지원 대상(assisted_role)과 새 도움 — 이슈 #6 준비 (제안)

D 코드에 들어간 준비 사항이다. 지원 대상 방식 자체는 #6에서 팀이 정하며, 기본값(부모)에서는 기존 동작과 같다.

| 항목 | 규칙 |
|---|---|
| HelpSnapshot.assisted_role | 이번 도움의 지원 대상 기기(PARENT/CHILD, 기본 PARENT). 도움마다 고정 |
| `help.link_approved` (B 서버 → D, 서버 내부) | payload `{assisted_role}`. 끝난 도움 뒤 같은 통화에서 새 코드가 승인되면 새 help_id로 다음 도움을 연다. 열린 도움에는 NOT_APPLICABLE, 재동의 전에는 NO_CONSENT |
| `POST /v1/calls` body | `{assisted_role}` 선택(기본 PARENT). 첫 도움의 지원 대상 |
| 앱 → 서버 권한 | 지원 대상: `projection.started`, `screen.observed`, `guide.*`, `help.stop` / 상대: `help.complete` / 둘 다: `consent.updated` |
| 서버 → 앱 | 지시·목표·모드·안내 효과와 안내 화면(`parent_view` 자리)은 지원 대상 기기로, 상대 기기에는 `call.invite`·`help.view`(최소 화면)·`arrival.*`·`help.ended`만 |
| 종료 사유 | 누른 가족 역할 + 동작: `CHILD_COMPLETED`·`PARENT_STOPPED`(부모 지원), `PARENT_COMPLETED`·`CHILD_STOPPED`(자녀 지원), `CONSENT_REVOKED`, `NO_GOAL_AT_HANGUP` |
| Basis | 새 키 없음(D 의견). 임시 연결 = help_id, assisted_role은 help_id에 고정 |

이름(`ParentView`/`ChildView`, `parent_view`/`child_view`)은 지금은 '지원 대상 화면/상대 화면' 뜻으로 쓰고, #6 합의 뒤 역할 중립 이름으로 바꾼다.
