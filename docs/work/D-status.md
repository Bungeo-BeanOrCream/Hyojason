현재 담당: Codex (N-24·N-25) · Claude Code (N-22·N-23, SessionControl 연동 2026-11-19 · B-05 연결 2026-11-20) · D 슬롯. N-11·N-12 변경은 커밋 902c36f~83c0187에 있다.

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
| B-05 연결 (D → C → D, `api/agent_bridge.py`) | C의 `B05Workflow`를 제어 서버에 연결(결정 메모 12). 발화 → 지시·목표 초안, 지시 → 대상 위치, 관찰 → 수행 판정·복귀·통화 후 안내, 도착 → N-22 → 자녀. 결과 이벤트는 지원 대상 기기에만. C 코드는 그대로, Zoro-1만 MOCK. FIXTURE 검증 | `tests/test_b05_bridge.py`, `test_control_server_agent.py` |
| [N-24 prepare_demo_fixtures](../small-nodes/N-24.md) | 두 AVD 및 시나리오 준비 검사 구현(Codex). 실제 AVD 상태 수집·설정 기록/원복·AVD 생성 안내 도구(`demo_env`) 추가. 가짜 adb로만 검증, 실제 AVD 미확인 | `tests/test_n24_prepare_demo_fixtures.py`, `test_n24_demo_env.py` |
| [N-25 evaluate_end_to_end](../small-nodes/N-25.md) | 지정 표본의 증거·출처·판정 및 모델·비용 보고 구현. FIXTURE 검증 | `tests/test_n25_evaluate_end_to_end.py` · [실행 방법](../../demo/fixtures/README.md) |

테스트: `cd server/session && python -m pytest` (Python 3.11+, pytest). 결과는 모두 source=FIXTURE이며 라이브 성공으로 세지 않는다.

## Mock으로 대체한 부분

| Mock | 대신하는 것 | 교체 시점 |
|---|---|---|
| 합성 통화·동의·발화, 화면 메타데이터, 지시·목표·위치·수행·완료 이벤트 | N-02·03·06·07·10·22의 인증·관찰·판정 결과. N-15·16·17·18·27·28은 B-05 연결 시험에서 C 실코드를 쓴다 | 각 생산 노드 연동 시 |
| `api.agent_bridge.fixture_zoro` | N-14 Zoro-1 호출 (C·L40S). 질문의 첫 선택지(또는 지정 선택지)를 0.95로 고름 | 실제 모델 경로는 준비됨: `HYOJASON_AGENT=zoro` + `ZORO_DECIDE_URL`·`ZORO_MODEL_REVISION`. L40S 접속 값(#23, SSH 터널)을 받으면 `tests/test_b05_zoro.py` 실제 모델 테스트로 확인 |
| speech.final 내부 경로 주입 | B-02 확정 발화 (B). `POST /v1/internal/calls/{id}/events`로 넣는다(#14 다음 계획 3) | B 음성 서버가 같은 경로로 보내면 교체 |
| `mocks.InMemoryHelpStore` | N-13·N-23 격리 단위 테스트의 N-11 상태 (실제 N-11은 `control.SessionControl`) | 격리 테스트 전용으로 유지 |
| `mocks.ScopedBuffer(NAVIGATION_TRAIL)` | 격리 테스트의 N-12 이력. 실제 경로는 `control._TrailPurge` | 격리 테스트 전용 |
| `mocks.ScopedBuffer(INFERENCE_RESULTS)` / `control._NoDataHeld` | B-05 판단 결과 (C). B-05 연결을 켜면 실제 저장소 `agent_bridge.InferenceResults`가 들어가 N-23이 도움 종료 때 지운다. 끄면 빈 자리 | 격리 테스트·연결 끔 상태 전용 |
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
   - 단위: ~~통화 1회에 1개~~ → **도움 1회에 1개**로 보완(결정 메모 9). 첫 도움은 통화를 만들 때(`POST /v1/calls`), 다음 도움은 새 코드 승인 때 새로 만든다.
   - 앱은 받은 값을 그대로 쓰고 새로 만들지 않는다. 앱이 다른 값을 보내면 서버는 통화의 값으로 기록한다.
   - 저장: DB 없이 서버 메모리의 통화 상태에만 두고 통화·도움 종료와 함께 정리한다. 상태는 계속 pair/call/help_id로 관리하며 trace_id는 기록을 묶는 꼬리표다.
   - 남은 합의: 제어 이벤트 봉투에 `trace_id` 필드 추가 (A·B, [계약 추가 제안 6장](D-role-view-contract.md)). 서버 구현은 제어 서버와 함께 한다 — 지금 `SessionControl`은 명령마다 새 trace_id를 만든다.

7. **웹 프레임워크는 FastAPI — 결정(2026-11-19, 사용자).** 서버가 Python 3.11+이고 WebSocket과 요청 형식 검증을 기본 지원한다. `server/session` 안에 두고 한 프로세스로 실행하며, 상태는 DB 없이 메모리(결정 메모 6). 엔드포인트 분담은 [이슈 #10](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/10)에 제안: D가 서버 뼈대·`WS /v1/events`·`GET /v1/help/current`, 페어·통화는 B 판단 함수를 D가 연결.

8. **새 도움 = 새 코드 승인 (G-3, 이슈 #6) — D 결정(2026-11-19).**
   - 도움이 끝나면 같은 RTC 통화 안에서도 B 서버가 보내는 `help.link_approved`(서버 내부 이벤트, payload `assisted_role`) 전에는 새 도움을 열지 않는다. 승인 전에 들어온 지시 발화는 해석하지 않는다(NOT_APPLICABLE).
   - 승인되면 새 help_id·빈 상태·캡처 미승인(새 projection_epoch 필요)에서 시작하고 지원 대상을 고정한다. 이전 도움을 대상으로 한 늦은 결과·종료는 help_id가 달라 STALE_BASIS.
   - 안내 끄기·동의 철회로 끝났으면 재동의 전에는 승인이 와도 열지 않는다(NO_CONSENT, B07.3).
   - 임시 연결(인증번호)은 help_id와 1:1로 본다. 도움이 끝나면 D가 `help.ended`를 보내고, B는 이 이벤트로 임시 연결을 닫는 것을 제안한다. 통화 종료는 도움을 끝내지 않는다(기존 POST_CALL_AGENT).
   - 기존 N-11의 '끝난 뒤 다음 발화로 자동 새 도움(rollover)'은 없앴다.
9. **trace_id는 도움 단위 — D 결정(2026-11-19).** #6 전제대로 새 도움 = 새 세션이므로 N-25가 한 실행에 두 도움을 섞지 않게 도움마다 새 trace_id를 쓴다. 통화 생성 응답의 trace_id는 첫 도움의 것이다.
10. **D 노드 역할 중립화 — 준비만(2026-11-19), 지원 대상 방식 자체는 #6 팀 결정.**
   - 도움마다 `assisted_role`(기본 부모)을 고정하고 D 코드가 이를 기준으로 동작한다: 관찰·안내 이벤트는 지원 대상만 보냄, 지시 발화자는 상대 역할, 안내·지시·목표 효과와 안내 화면은 지원 대상 기기로, 재접속 재관찰은 지원 대상만, 완료는 상대·안내 끄기는 지원 대상(#6 제안).
   - 종료 사유에 실제로 누른 가족 역할이 들어간다: 부모 지원이면 기존 그대로 `CHILD_COMPLETED`/`PARENT_STOPPED`, 자녀 지원이면 새로 추가한 `PARENT_COMPLETED`/`CHILD_STOPPED`.
   - 기본값(부모)에서는 기존 동작과 같다. 타입·필드 이름(ParentView/ChildView, 메서드 이름)은 A처럼 #6 합의 뒤 한 번에 역할 중립 이름으로 바꾼다.
   - 남은 팀 결정: assisted_role 도입과 기본값, 완료 권한, 자녀 발신 P0(지금 D 서버는 부모 발신만), Basis·contracts.md 변경. D 의견: Basis에 새 키는 필요 없다(임시 연결 = help_id, assisted_role은 help_id에 고정, 캡처는 projection_epoch).

11. **시연 기계는 팀 Apple Silicon Mac — 결정(2026-11-19, 사용자).** 설계(ARM64 AVD, 같은 Mac에 두 AVD)대로 간다. 그 Mac에서 `bash demo/fixtures/setup_avd_mac.sh` 한 번으로 AVD 두 대(부모 5554·자녀 5556, Android 15·ko-KR·Asia/Seoul) 생성·부팅·상태 수집까지 한다. 최종 리허설과 시연을 같은 기계에서 해 N-25 기록의 기기 정보가 시연과 일치하게 한다. 어느 팀원의 Mac인지는 아직 모른다.
12. **B-05 연결 방식 — D 결정(2026-11-20).** C의 README '연결'과 contracts 이벤트 표를 따른다. (1) N-11이 이벤트를 받고 앱 전달을 마친 뒤 C를 부른다 — 부모 t0(instruction.accepted)가 위치 탐색을 기다리지 않는다. (2) C에는 같은 순간의 snapshot·N-12 이력·처리권(양쪽 동의, 도움 진행 중)을 한 번에 넘기고, 결과는 그 순간의 Basis로 N-11에 넣는다. 오래된 결과는 C의 `read_current`와 N-11의 `STALE_BASIS`가 두 번 거른다. (3) target.located·action.assessed는 N-11이 받아들였을 때만, recovery.ready·guide.proposed는 판단 범위가 최신일 때만 지원 대상 기기로 보낸다. 도착은 N-22를 거친다. (4) 서버에서는 작업 스레드 하나가 차례로 처리해 모델 호출이 WS 이벤트 루프를 막지 않는다. (5) C의 결과 근거 코드(`visible_target`, `verified_reverse_edge`, `observed:<대상>`)를 카탈로그에 더했다(`catalog.B05_RESULT_EVIDENCE`). 화면 관찰 근거 형식은 A·C·D 합의 대상이라 더하지 않았다(아래 '막힌 것').

## 다음 할 일

1. **제어 서버 — 뼈대 완료, 남은 연결.** FastAPI 뼈대(결정 메모 7)에 수신·송신 경로와 N-21 화면 재전송까지 들어갔다. 남은 것:
   - 이슈 #10: B의 페어·통화·토큰 판단 함수와 인증 토큰 형식을 받아 `MockPairingService`·`MockTokenAuthenticator`를 교체.
   - 봉투 `trace_id` 합의(아래 2) 뒤 송신 이벤트에 싣기. 지금 trace_id는 `POST /v1/calls` 응답과 서버 내부 결과에만 있다.
   - A·B 앱이 WS에 붙어 실제 메시지로 확인 (지금은 테스트 클라이언트·uvicorn 스모크뿐).
   - B-05 연결(결정 메모 12)은 끝났다(FIXTURE). 실제 Zoro-1은 `HYOJASON_AGENT=zoro`로 켠다. 접속 값이 없으면 서버가 뜨지 않고, 모델 접속 실패·마감 초과 때는 지시만 가고 위치 근거는 보내지 않는다(`tests/test_b05_zoro.py`). D 환경에서 실제 L40S 호출은 아직 못 했다(UNVERIFIED). 위험 동작 문구(`B05Output.warning`)를 부모에게 보내는 이벤트는 계약에 없어 아직 보내지 않는다 — contracts PR에서 정한다.
2. **이슈 #6 합의**: 결정 메모 10의 남은 팀 결정. 합의되면 이름 변경과 `docs/contracts.md` 반영, B 서버(`server/voice`)의 `help.link_approved` 연결.
3. **제어 이벤트 봉투에 `trace_id` 추가 합의** (이슈 #5, A·B). 발급은 서버로 결정됨(결정 메모 6). 합의되면 `docs/contracts.md` PR에 함께 넣고, 제어 서버 구현 때 통화 단위 trace_id를 발급·전달한다.
4. [계약 추가 제안](D-role-view-contract.md)을 B·C와 확인 → `docs/contracts.md` 반영 (이슈 #2: A 동의, B·C 답변 대기).
5. ~~D → main PR~~ — PR #17로 머지됨(2026-11-19). 이후 main을 D에 반영(2026-11-20, 충돌 없음).
6. 이슈 #3의 [확정 계약](../screen-observation-profile.md)을 A의 운영 프로필에 연결하고 AVD에서 확인.
7. **시연 AVD 준비 (N-24)**: 팀 Mac(결정 메모 11)에서 `setup_avd_mac.sh` 실행 → 사람 확인 항목 채우기 → `demo_verify prepare --source LIVE`. 효자손 APK(A·B)가 나오기 전에는 `ROLE_APP_NOT_READY`로 막힌다.
8. **C·D 계약 차이 맞추기 (C와 협의)**: 아래 '막힌 것'의 목표 슬롯 이름과 화면 근거 형식. 맞추기 전에는 SET_TIMEOUT만 C → D → 자녀 도착까지 이어진다.
9. [N-24·N-25 리허설 준비와 검증](../../demo/fixtures/README.md)에 따라 A·B 각 1회의 증거 경로를 먼저 연결하고, 각 5회 및 고정 표본을 확인 (이슈 #5).

## 하면 안 되는 일

- 다른 슬롯 경로(`android/*`, `server/agent`) 수정, `main` 직접 푸시.
- 합의 없이 `docs/contracts.md`, `docs/node-result-contract.md` 수정.
- 자녀 응답에 목표·지시·화면·좌표·이력 싣기, 재접속에서 과거 Overlay·GuideProposal 재생.
- 종료 기록·이벤트에 사용자 원문·화면 내용 넣기 (식별자와 종료 사유만).
- 같은 D 브랜치에서 두 도구가 같은 파일을 동시에 고치기 (AGENTS.md: 한 worktree는 한 도구).
- FIXTURE 테스트 통과를 실기기 PASS로 기록.

## 막힌 것

- 이슈 #2: B(ChildView)·C(HelpSnapshot 추가 필드) 답변 대기.
- 시연용 팀 Mac의 담당자·기종(Apple Silicon 여부)이 아직 확인되지 않음. 확인되면 그 Mac에서 `setup_avd_mac.sh` 실행.
- 이슈 #6: 지원 대상 방식·완료 권한·자녀 발신·Basis는 팀 합의 대기 (A·B는 의견 남김, C 대기).
- 이슈 #10: B가 분담 동의, 함수 위치(`server/voice/hyojason_voice/pair.py`·`consent.py`·`call.py`), opaque Bearer 토큰을 제안했다. D 동의 답변은 아직 안 올림. B 코드가 main에 오기 전까지 페어·통화 자리에 Mock.
- 이슈 #5: A는 AVD 실측 전이고 B·C 증거 위치 답변 대기. trace_id는 서버 발급으로 답함 — 봉투 필드 추가는 A·B 확인 대기.
- 이슈 #3의 실제 앱별 프로필과 screen_revision 규칙은 AVD에서 확인해야 한다.
- **실제 Zoro-1 접속 값 없음**: #23에서 C가 L40S VM에 배포했고 Mac에서는 SSH 터널로 접속한다. 주소·리비전은 팀 런타임 환경에만 있어 D는 아직 실제 모델로 `test_b05_zoro.py`를 돌리지 못했다. C에게 터널 접속을 받거나 C가 이 테스트를 돌려 줘야 한다.
- **C의 통합 백엔드와 겹침**: #23의 `server.mvp:create_app`(B → D → C → Zoro-1)은 원격 브랜치에 없다(C 로컬 작업본). D의 `api/agent_bridge.py`와 같은 일을 하므로 어느 쪽을 쓸지 C와 정해야 한다. C 쪽에서 B의 call_id를 D에 넘길 때 404가 났다고 하니 B ↔ D 통화 식별자 연결(#10)도 함께 맞춘다.
- **C(B-05)와 D(이슈 #3·goals.json) 계약 차이** — C 코드(`server/agent`)는 D가 고치지 않는다. C와 합의해 한쪽을 맞춘다.
  - 목표 슬롯: SET_FONT `delta`(C) ↔ `steps`(D), MAP_ROUTE `destination_id: demo_place` ↔ `place: demo-…`, SMS_SEND `demo_recipient·demo_message` ↔ `demo-recipient·demo-sms`, MAIL_OPEN `demo_mail` ↔ `demo-mail`. 지금은 N-11이 C의 목표 초안을 거절한다(`test_known_gap_c_goal_slots_differ_from_d_catalog`). SET_TIMEOUT·CLOCK_ALARM은 일치.
  - 도착 근거: CLOCK_ALARM `alarm_next:<날짜>:<시각>`(C) ↔ `alarm_next_tomorrow`(이슈 #3 합의). MAP_ROUTE·SMS_SEND도 C가 `route_destination:…`·`sms_sent:…`를 더 요구한다. A가 합의대로 보내면 C가 도착을 못 찾고, C 형식으로 보내면 D 카탈로그가 거절한다.
  - 화면 아래·수행 근거: C가 제안한 `scroll_down:<영역>`·`below_fold:<영역>:<대상>`·`activated:<대상>`은 A의 N-09 공급과 D 카탈로그 합의가 필요하다. N-11의 수행 판정(연속 전이)은 새 화면에 근거 코드가 하나 이상 있어야 해서 A의 탐지 근거 코드가 정해져야 한다(시험은 `navigation_transition`으로 대신).
  - C는 자녀 발화만 지시로 본다(`speaker_role == child`). #6에서 자녀가 지원 대상인 도움을 넣으면 C도 바뀌어야 한다.
- 이슈 #1 Android 골격 전: 클라이언트 쪽 N-13·N-23 처리의 실제 구현·검증 불가.
- 두 AVD의 실제 통화·캡처·자산과 L40S 모델·비용 증거가 모이기 전까지 N-24·N-25의 라이브 결과는 `UNVERIFIED`.
