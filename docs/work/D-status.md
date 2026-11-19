현재 담당: Codex (2026-11-19) · D 슬롯 N-24·N-25. 같은 날 Claude Code가 N-22·N-23을 구현했다. N-11·N-12 변경은 커밋 902c36f~83c0187에 있다.

# D 작업 상태 (B-04 · B-07 · B-08 통합)

## 현재 상태

| 노드 | 상태 | 근거 |
|---|---|---|
| [N-11 reduce_help_state](../small-nodes/N-11.md) | 서버 상태 처리 및 N-13·N-21 읽기 연결 구현. FIXTURE 검증 | `tests/test_help_state.py`, `test_session_control.py` |
| [N-12 record_verified_navigation](../small-nodes/N-12.md) | 메모리 이력 및 공백 처리 구현. FIXTURE 검증 | `tests/test_navigation.py`, `test_session_control.py` |
| [N-13 reconcile_reconnect](../small-nodes/N-13.md) | 서버 로직 + 클라이언트 참조 구현, FIXTURE 테스트 통과. **실기기 UNVERIFIED** | `tests/test_n13_reconcile_reconnect.py` (13.1~13.4 + 완료 기준) |
| [N-21 project_role_view](../small-nodes/N-21.md) | 서버 로직, FIXTURE 테스트 통과. **실기기 UNVERIFIED** | `tests/test_n21_project_role_view.py` (21.1~21.4 + 완료 기준) |
| [N-22 resolve_arrival_or_completion](../small-nodes/N-22.md) | 서버 로직, FIXTURE 테스트 통과. **SessionControl(N-11) 연동 전**, 실기기 UNVERIFIED | `tests/test_n22_resolve_arrival_or_completion.py` (22.1~22.5 + 완료 기준, 동시 종료) |
| [N-23 purge_help_and_restart](../small-nodes/N-23.md) | 서버 로직 + 클라이언트 참조 구현, FIXTURE 테스트 통과. **SessionControl(N-11) 연동 전**, 실기기 UNVERIFIED | `tests/test_n23_purge_help_and_restart.py` (23.1~23.5 + 완료 기준) |
| B-07 흐름 (N-21→N-22→N-23→N-13) | 격리 Mock(InMemoryHelpStore) 위 테스트 배선으로 확인. Big Node 오케스트레이션 코드는 아직 없음 | `tests/test_b07_integration.py` (B07.1~B07.4) |
| [N-24 prepare_demo_fixtures](../small-nodes/N-24.md) | 두 AVD 및 시나리오 준비 검사 구현. FIXTURE 검증 | `tests/test_n24_prepare_demo_fixtures.py` |
| [N-25 evaluate_end_to_end](../small-nodes/N-25.md) | 지정 표본의 증거·출처·판정 및 모델·비용 보고 구현. FIXTURE 검증 | `tests/test_n25_evaluate_end_to_end.py` · [실행 방법](../../demo/fixtures/README.md) |

테스트: `cd server/session && python -m pytest` (Python 3.11+, pytest). 결과는 모두 source=FIXTURE이며 라이브 성공으로 세지 않는다.

## Mock으로 대체한 부분

| Mock | 대신하는 것 | 교체 시점 |
|---|---|---|
| 합성 통화·동의·발화, 화면 메타데이터, 지시·목표·위치·수행·완료 이벤트 | N-02·03·06·07·10·15·22·27·28의 인증·관찰·판정 결과 | 각 생산 노드 연동 시 |
| `mocks.InMemoryHelpStore` | N-13·N-23 격리 단위 테스트의 N-11 상태 (실제 N-11은 `control.SessionControl`) | 격리 테스트 전용으로 유지 |
| `mocks.ScopedBuffer(NAVIGATION_TRAIL)` | N-23이 비울 N-12 이력. 실제 `navigation.NavigationTrail`은 N-11 종료 시 trail.clear로 비워진다 | 결정 메모 5 정리 시 |
| `mocks.ScopedBuffer(INFERENCE_RESULTS)` | B-05 추론 후보·제안·판단 결과 (C) | C의 결과 저장 연동 시 |
| `mocks.ScopedBuffer(PENDING_UTTERANCES)` | B-02 대기 확정 발화 (B) | B의 발화 큐 연동 시 |
| `mocks.RecordingEventSink` | WS /v1/events 제어 채널 (D) | 제어 엔드포인트 구현 시 |
| `mocks.StaticPairDirectory` | N-01 resolve_pair 페어 승인 (B) | B의 N-01 연동 시 |
| `ActorContext` 직접 주입 | B-01 인증 토큰 검증 (B) | 제어 엔드포인트 구현 시 |
| `client_resume_gate.py`, `client_cleanup.py` (Python) | 부모·자녀 앱의 재접속·종료 처리 (A·B, Kotlin) | `android/contract` 골격(이슈 #1) 뒤 A·B가 이식 |
| `manifest.fixture.json`, `evidence.fixture.json` | AVD·앱·권한·연결 점검과 소수 합성 실행 기록 | A·B·C의 실측 증거 수집 후 `LIVE`로 별도 평가 |

N-22 장부(`completion.CompletionLedger`)와 N-23 종료 기록(`purge.TombstoneRegistry`)은 Mock이 아닌 실제 구현이다(도움별 메모리 상태).

## 결정 메모 (팀 공유 필요)

1. **완료·중단 명령은 help_id로만 범위 확인.** 자녀는 N-21에 따라 목표 정보를 받지 않아 goal_revision을 알 수 없다. 부모 안내 끄기도 N-23 로컬 우선 규칙상 목표 변경을 이유로 거절하면 안 된다. 도착 추정(ArrivalDecision)은 help·goal·mode·동의·화면 버전을 모두 대조한다.
2. **B-07 라우팅 규칙.** 이미 지워진 도움의 두 번째 완료·중단은 N-22에 넘길 snapshot이 없다. B-07 정책 3대로 "페어의 최신 도움이고 종료 기록이 있음"이면 N-23(already_ended, 화면 효과 0), 더 오래된 도움이면 N-22(STALE_BASIS)로 보낸다.
3. **새 도움 열기 조건.** N-23이 종료 기록에 `resume_requires_consent`(부모 중단·동의 철회)를 남긴다. N-11은 새 자녀 지시·목표로 도움을 열기 전에 `TombstoneRegistry.latest_for_pair(pair_id)`를 확인해야 한다 (B07.3).
4. **도착 상태는 (goal, mode) revision에 묶임.** 목표가 바뀌면 이전 도착 추정은 자녀에게 보이지 않는다.
5. **N-11과 N-22·N-23의 역할 겹침 (결정 필요).** 병행 작업의 N-11(`help_state.py`)이 `help.complete`/`help.stop`/`arrival.*`를 직접 받아 종료·도착 상태를 바꾸고, 종료 때 상태를 비운다. 계약상 흐름은 `help.complete → N-22 승인 → N-23 정리·help.ended → N-11 반영`이다(N-11 문서의 입력 작성자 "N-22 결과"). 또 N-11은 `help.complete`에 goal_revision·consent_revision을 요구해 결정 메모 1과 충돌한다. 둘 중 하나로 정해야 한다.
   - (가) N-11은 N-22가 승인한 종료·도착만 반영하고, 정리와 help.ended 발행은 N-23이 맡는다. `SessionControl`에 N-22·N-23을 연결한다.
   - (나) N-11의 직접 처리를 유지하고, N-22는 중복·권한 판정만, N-23은 N-11이 비우지 않는 범위(추론 결과·대기 발화)와 단말 전달만 맡는다.

## 다음 할 일

1. 결정 메모 5를 정한 뒤 `SessionControl`에 N-22·N-23 연결, `InMemoryHelpStore` 대신 실제 N-11로 B-07 통합 테스트.
2. 이슈 #3의 [확정 계약](../screen-observation-profile.md)을 A의 운영 프로필에 연결하고 AVD에서 확인.
3. [계약 추가 제안](D-role-view-contract.md)을 A·B·C와 확인 → `docs/contracts.md` 반영 (이슈 #2: A 동의, B·C 답변 대기).
4. B-07 오케스트레이션(이벤트 → N-21/N-22/N-23 라우팅, 결정 메모 2).
5. `GET /v1/help/current`·`WS /v1/events` 엔드포인트 (웹 프레임워크 선택 필요).
6. [N-24·N-25 리허설 준비와 검증](../../demo/fixtures/README.md)에 따라 A·B 각 1회의 증거 경로를 먼저 연결하고, 각 5회 및 고정 표본을 확인.

## 하면 안 되는 일

- 다른 슬롯 경로(`android/*`, `server/agent`) 수정, `main` 직접 푸시.
- 합의 없이 `docs/contracts.md`, `docs/node-result-contract.md` 수정.
- 자녀 응답에 목표·지시·화면·좌표·이력 싣기, 재접속에서 과거 Overlay·GuideProposal 재생.
- 종료 기록·이벤트에 사용자 원문·화면 내용 넣기 (식별자와 종료 사유만).
- 같은 D 브랜치에서 두 도구가 같은 파일을 동시에 고치기 (AGENTS.md: 한 worktree는 한 도구).
- FIXTURE 테스트 통과를 실기기 PASS로 기록.

## 막힌 것

- 결정 메모 5: N-11과 N-22·N-23의 역할 분담.
- 이슈 #2: B(ChildView)·C(HelpSnapshot 추가 필드) 답변 대기.
- 이슈 #3의 실제 앱별 프로필과 screen_revision 규칙은 AVD에서 확인해야 한다.
- 이슈 #1 Android 골격 전: 클라이언트 쪽 N-13·N-23 처리의 실제 구현·검증 불가.
- 두 AVD의 실제 통화·캡처·자산과 L40S 모델·비용 증거가 모이기 전까지 N-24·N-25의 라이브 결과는 `UNVERIFIED`.
