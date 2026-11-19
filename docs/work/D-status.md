현재 담당: Codex (N-24·N-25) · Claude Code (N-22·N-23, SessionControl 연동) (2026-11-19) · D 슬롯. N-11·N-12 변경은 커밋 902c36f~83c0187에 있다.

# D 작업 상태 (B-04 · B-07 · B-08 통합)

## 현재 상태

| 노드 | 상태 | 근거 |
|---|---|---|
| [N-11 reduce_help_state](../small-nodes/N-11.md) | 서버 상태 처리 및 N-13·N-21 읽기 연결 구현. 완료·중단은 N-22가 승인한 `help.end`만 받음(결정 메모 5). FIXTURE 검증 | `tests/test_help_state.py`, `test_session_control.py` |
| [N-12 record_verified_navigation](../small-nodes/N-12.md) | 메모리 이력 및 공백 처리 구현. FIXTURE 검증 | `tests/test_navigation.py`, `test_session_control.py` |
| [N-13 reconcile_reconnect](../small-nodes/N-13.md) | 서버 로직 + 클라이언트 참조 구현, FIXTURE 테스트 통과. **실기기 UNVERIFIED** | `tests/test_n13_reconcile_reconnect.py` (13.1~13.4 + 완료 기준) |
| [N-21 project_role_view](../small-nodes/N-21.md) | 서버 로직, FIXTURE 테스트 통과. **실기기 UNVERIFIED** | `tests/test_n21_project_role_view.py` (21.1~21.4 + 완료 기준) |
| [N-22 resolve_arrival_or_completion](../small-nodes/N-22.md) | 서버 로직 + `SessionControl.submit_command/submit_arrival` 연동, FIXTURE 테스트 통과. 실기기 UNVERIFIED | `tests/test_n22_resolve_arrival_or_completion.py` (22.1~22.5 + 완료 기준, 동시 종료), `test_session_completion.py` |
| [N-23 purge_help_and_restart](../small-nodes/N-23.md) | 서버 로직 + 클라이언트 참조 구현 + N-11 `help.purge` 효과로 모든 종료에 연동, FIXTURE 테스트 통과. 실기기 UNVERIFIED | `tests/test_n23_purge_help_and_restart.py` (23.1~23.5 + 완료 기준), `test_session_completion.py` |
| B-07 흐름 (N-22→N-11→N-23→N-13) | 실제 N-11(`SessionControl`) 위에서 확인. 제어 서버 경로로도 확인(`test_control_server.py`) | `tests/test_session_completion.py`, 격리판 `tests/test_b07_integration.py` (B07.1~B07.4) |
| 제어 서버 (FastAPI, `hyojason_session/api`) | 뼈대 구현: `WS /v1/events`, `GET /v1/help/current`, 페어·통화 경로(MOCK), 서버 내부 경로. 실제 uvicorn으로 기동·WS 왕복 확인. 결과는 FIXTURE | `tests/test_control_server.py` |
| [N-24 prepare_demo_fixtures](../small-nodes/N-24.md) | 두 AVD 및 시나리오 준비 검사 구현. FIXTURE 검증 | `tests/test_n24_prepare_demo_fixtures.py` |
| [N-25 evaluate_end_to_end](../small-nodes/N-25.md) | 지정 표본의 증거·출처·판정 및 모델·비용 보고 구현. FIXTURE 검증 | `tests/test_n25_evaluate_end_to_end.py` · [실행 방법](../../demo/fixtures/README.md) |

테스트: `cd server/session && python -m pytest` (Python 3.11+, pytest). 결과는 모두 source=FIXTURE이며 라이브 성공으로 세지 않는다.

## Mock으로 대체한 부분

| Mock | 대신하는 것 | 교체 시점 |
|---|---|---|
| 합성 통화·동의·발화, 화면 메타데이터, 지시·목표·위치·수행·완료 이벤트 | N-02·03·06·07·10·15·22·27·28의 인증·관찰·판정 결과 | 각 생산 노드 연동 시 |
| `mocks.InMemoryHelpStore` | N-13·N-23 격리 단위 테스트의 N-11 상태 (실제 N-11은 `control.SessionControl`) | 격리 테스트 전용으로 유지 |
| `mocks.ScopedBuffer(NAVIGATION_TRAIL)` | 격리 테스트의 N-12 이력. 실제 경로는 `control._TrailPurge` | 격리 테스트 전용 |
| `mocks.ScopedBuffer(INFERENCE_RESULTS)` / `control._NoDataHeld` | B-05 추론 후보·제안·판단 결과 (C). 세션 서버에 아직 저장소가 없어 실제 경로는 빈 자리 | C의 결과 저장이 서버에 생기면 `result_stores`로 연결 |
| `mocks.ScopedBuffer(PENDING_UTTERANCES)` / `control._NoDataHeld` | B-02 대기 확정 발화 (B). 실제 경로는 빈 자리 | B의 발화 큐가 서버에 생기면 `result_stores`로 연결 |
| `mocks.RecordingEventSink` | WS /v1/events 제어 채널 (D). `SessionControl(events=...)`로 주입, 없으면 전달 실패로 기록 | 제어 엔드포인트 구현 시 |
| `mocks.StaticPairDirectory` | N-01 resolve_pair 페어 승인 (B), N-13 격리 테스트 | B의 N-01 연동 시 |
| `mocks.MockPairingService` | N-01 페어 승인·N-03 통화 수락 시 RTC 연결 확인·LiveKit 토큰(null) (B) | 이슈 #10에서 B 판단 함수를 받으면 교체 |
| `mocks.MockTokenAuthenticator` | B-01 인증 토큰 검증 (B). 고정 가짜 토큰 → 역할·페어 | 이슈 #10 3번(토큰 형식) 답 뒤 교체 |
| `client_resume_gate.py`, `client_cleanup.py` (Python) | 부모·자녀 앱의 재접속·종료 처리 (A·B, Kotlin) | `android/contract` 골격(이슈 #1) 뒤 A·B가 이식 |
| `manifest.fixture.json`, `evidence.fixture.json` | AVD·앱·권한·연결 점검과 소수 합성 실행 기록 | A·B·C의 실측 증거 수집 후 `LIVE`로 별도 평가 |

N-22 장부(`completion.CompletionLedger`)와 N-23 종료 기록(`purge.TombstoneRegistry`)은 Mock이 아닌 실제 구현이다(도움별 메모리 상태).

## 결정 메모 (팀 공유 필요)

1. **완료·중단 명령은 help_id로만 범위 확인.** 자녀는 N-21에 따라 목표 정보를 받지 않아 goal_revision을 알 수 없다. 부모 안내 끄기도 N-23 로컬 우선 규칙상 목표 변경을 이유로 거절하면 안 된다. 도착 추정(ArrivalDecision)은 help·goal·mode·동의·화면 버전을 모두 대조한다.
2. **끝난 도움에 다시 온 명령.** `SessionControl`은 끝난 snapshot을 새 도움이 열릴 때까지 들고 있어 두 번째 완료·중단은 N-22가 `duplicate_ignored`로, 새 도움이 열린 뒤의 이전 help_id는 `STALE_BASIS`로 처리한다. snapshot까지 지워지는 배선(격리판 `test_b07_integration.py`)에서는 B-07 정책 3대로 "최신 도움이고 종료 기록이 있음"이면 N-23(already_ended)으로 보낸다.
3. **새 도움 열기 조건.** N-23이 종료 기록에 `resume_requires_consent`(부모 중단·동의 철회)를 남긴다. 실제 차단은 N-11이 동의 집합으로 한다: 부모 중단은 부모 동의를 해제해 재동의 전 발화가 NO_CONSENT (B07.3, `test_session_completion.py`).
4. **도착 상태는 (goal, mode) revision에 묶임.** 목표가 바뀌면 이전 도착 추정은 자녀에게 보이지 않는다.
5. **N-11과 N-22·N-23의 역할 분담 — (가) 계약 흐름으로 결정(2026-11-19, 사용자).**
   - 완료·중단: `SessionControl.submit_command` → N-22 승인 → N-11 `help.end`(서버 내부 이벤트) → N-23 정리·`help.ended`. 자녀·부모는 `help.end`를 직접 보낼 수 없고, 예전 `help.complete`/`help.stop`은 N-11 입력이 아니다.
   - 도착: `SessionControl.submit_arrival` → N-22 검증·중복 제거 → N-11 `arrival.suspected/revoked`. 철회는 앞서 알린 arrival_id를 가리킨다.
   - N-11 내부 종료(동의 철회 `CONSENT_REVOKED`, 목표 없는 통화 종료 `NO_GOAL_AT_HANGUP`)도 N-11의 `help.purge` 효과로 N-23을 거쳐 `help.ended`를 보낸다. N-11은 `help.ended`를 직접 내지 않는다.

6. **trace_id는 서버가 발급 — 결정(2026-11-19, 사용자).** 이슈 #5의 A 질문에 대한 답.
   - 단위: 통화(call) 1회에 1개. 서버가 통화를 만들 때(`POST /v1/calls`, 통화별 `SessionControl` 생성 시점) 만들고, 같은 통화 안에서 help_id가 바뀌어도 유지한다.
   - 앱은 받은 값을 그대로 쓰고 새로 만들지 않는다. 앱이 다른 값을 보내면 서버는 통화의 값으로 기록한다.
   - 저장: DB 없이 서버 메모리의 통화 상태에만 두고 통화·도움 종료와 함께 정리한다. 상태는 계속 pair/call/help_id로 관리하며 trace_id는 기록을 묶는 꼬리표다.
   - 남은 합의: 제어 이벤트 봉투에 `trace_id` 필드 추가 (A·B, [계약 추가 제안 6장](D-role-view-contract.md)). 서버 구현은 제어 서버와 함께 한다 — 지금 `SessionControl`은 명령마다 새 trace_id를 만든다.

7. **웹 프레임워크는 FastAPI — 결정(2026-11-19, 사용자).** 서버가 Python 3.11+이고 WebSocket과 요청 형식 검증을 기본 지원한다. `server/session` 안에 두고 한 프로세스로 실행하며, 상태는 DB 없이 메모리(결정 메모 6). 엔드포인트 분담은 [이슈 #10](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/10)에 제안: D가 서버 뼈대·`WS /v1/events`·`GET /v1/help/current`, 페어·통화는 B 판단 함수를 D가 연결.

## 다음 할 일

1. **제어 서버 — 뼈대 완료, 남은 연결.** FastAPI 뼈대(결정 메모 7)에 수신·송신 경로와 N-21 화면 재전송까지 들어갔다. 남은 것:
   - 이슈 #10: B의 페어·통화·토큰 판단 함수와 인증 토큰 형식을 받아 `MockPairingService`·`MockTokenAuthenticator`를 교체.
   - 봉투 `trace_id` 합의(아래 2) 뒤 송신 이벤트에 싣기. 지금 trace_id는 `POST /v1/calls` 응답과 서버 내부 결과에만 있다.
   - A·B 앱이 WS에 붙어 실제 메시지로 확인 (지금은 테스트 클라이언트·uvicorn 스모크뿐).
2. **제어 이벤트 봉투에 `trace_id` 추가 합의** (이슈 #5, A·B). 발급은 서버로 결정됨(결정 메모 6). 합의되면 `docs/contracts.md` PR에 함께 넣고, 제어 서버 구현 때 통화 단위 trace_id를 발급·전달한다.
3. [계약 추가 제안](D-role-view-contract.md)을 B·C와 확인 → `docs/contracts.md` 반영 (이슈 #2: A 동의, B·C 답변 대기).
4. D → main PR. Codex가 D에서 고친 공유 문서(`docs/contracts.md` 한 줄, `docs/screen-observation-profile.md`, `docs/verification-plan.md` E09)는 이슈 #3 합의분이므로 A 리뷰를 받는다.
5. 이슈 #3의 [확정 계약](../screen-observation-profile.md)을 A의 운영 프로필에 연결하고 AVD에서 확인.
6. [N-24·N-25 리허설 준비와 검증](../../demo/fixtures/README.md)에 따라 A·B 각 1회의 증거 경로를 먼저 연결하고, 각 5회 및 고정 표본을 확인 (이슈 #5).

## 하면 안 되는 일

- 다른 슬롯 경로(`android/*`, `server/agent`) 수정, `main` 직접 푸시.
- 합의 없이 `docs/contracts.md`, `docs/node-result-contract.md` 수정.
- 자녀 응답에 목표·지시·화면·좌표·이력 싣기, 재접속에서 과거 Overlay·GuideProposal 재생.
- 종료 기록·이벤트에 사용자 원문·화면 내용 넣기 (식별자와 종료 사유만).
- 같은 D 브랜치에서 두 도구가 같은 파일을 동시에 고치기 (AGENTS.md: 한 worktree는 한 도구).
- FIXTURE 테스트 통과를 실기기 PASS로 기록.

## 막힌 것

- 이슈 #2: B(ChildView)·C(HelpSnapshot 추가 필드) 답변 대기.
- 이슈 #10: 엔드포인트 분담에 대한 B 동의, B 판단 함수 위치, 인증 토큰 형식 답변 대기. 그 전에는 페어·통화 자리에 Mock.
- 이슈 #5: A는 AVD 실측 전이고 B·C 증거 위치 답변 대기. trace_id는 서버 발급으로 답함 — 봉투 필드 추가는 A·B 확인 대기.
- 이슈 #3의 실제 앱별 프로필과 screen_revision 규칙은 AVD에서 확인해야 한다.
- 이슈 #1 Android 골격 전: 클라이언트 쪽 N-13·N-23 처리의 실제 구현·검증 불가.
- 두 AVD의 실제 통화·캡처·자산과 L40S 모델·비용 증거가 모이기 전까지 N-24·N-25의 라이브 결과는 `UNVERIFIED`.
