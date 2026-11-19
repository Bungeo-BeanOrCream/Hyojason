현재 담당: Claude Code (2026-11-19) · D 슬롯

# D 작업 상태 (B-04 · B-07 · B-08 통합)

## 현재 상태

| 노드 | 상태 | 근거 |
|---|---|---|
| [N-21 project_role_view](../small-nodes/N-21.md) | 서버 로직 구현, FIXTURE 테스트 통과. **실기기 UNVERIFIED** | `server/session/tests/test_n21_project_role_view.py` (21.1~21.4 + 완료 기준) |
| [N-13 reconcile_reconnect](../small-nodes/N-13.md) | 서버 로직 + 클라이언트 참조 구현, FIXTURE 테스트 통과. **실기기 UNVERIFIED** | `server/session/tests/test_n13_reconcile_reconnect.py` (13.1~13.4 + 완료 기준) |
| [N-11 reduce_help_state](../small-nodes/N-11.md) | 서버 상태 처리 및 N-13·N-21 읽기 연결 구현. FIXTURE 검증 | `server/session/tests/test_help_state.py`, `test_session_control.py` |
| [N-12 record_verified_navigation](../small-nodes/N-12.md) | 메모리 이력 및 공백 처리 구현. FIXTURE 검증 | `server/session/tests/test_navigation.py`, `test_session_control.py` |
| N-22, N-23, N-24, N-25 | 후속 작업 | — |

테스트: `cd server/session && python -m pytest` (Python 3.11+, pytest). 결과는 모두 source=FIXTURE이며 라이브 성공으로 세지 않는다.

## Mock으로 대체한 부분

| Mock | 대신하는 것 | 교체 시점 |
|---|---|---|
| 합성 통화·동의·발화, 화면 메타데이터, 지시·목표·위치·수행·완료 이벤트 | N-02·03·06·07·10·15·22·27·28의 인증·관찰·판정 결과 | 각 생산 노드 연동 시 |
| `mocks.InMemoryHelpStore` (latest_help_id, get_snapshot) | 격리된 N-13 단위 테스트의 권위 상태 | N-13 연동 테스트에서는 `SessionControl` 사용 |
| `mocks.InMemoryHelpStore.arrival_state` | N-22 도착 추정 알림 상태 (D) | N-22 구현 시 |
| `mocks.InMemoryHelpStore.get_tombstone / end_help` | N-23 종료 후 최소 기록 (D) | N-23 구현 시 |
| `mocks.StaticPairDirectory` | N-01 resolve_pair 페어 승인 (B) | B의 N-01 연동 시 |
| `ActorContext` 직접 주입 | B-01 인증 토큰 검증 (B) | 제어 엔드포인트 구현 시 |
| `client_resume_gate.py` (Python) | 부모·자녀 앱의 재접속 처리 (A·B, Kotlin) | Android 골격(이슈 #1) 이후 A·B가 이식 |

## 다음 할 일

1. 이슈 #3의 [D 검토본](D-issue3-screen-kind-review.md)을 A와 확정한 뒤 공식 문서와 운영 프로필에 반영.
2. 이슈 #2: 서버는 Python, 타입 공유는 `server/session/schemas/` JSON Schema로 답하기.
3. [계약 추가 제안](D-role-view-contract.md)을 A·B·C와 확인 → `docs/contracts.md` PR.
4. N-22·N-23에서 도착 추정·종료 승인과 후속 정리를 연동.
5. `GET /v1/help/current`·`WS /v1/events` 재접속 엔드포인트에 N-13 연결 (웹 프레임워크 선택 필요).

## 하면 안 되는 일

- 다른 슬롯 경로(`android/*`, `server/agent`) 수정, `main` 직접 푸시.
- 합의 없이 `docs/contracts.md`, `docs/node-result-contract.md` 수정.
- 자녀 응답에 목표·지시·화면·좌표·이력 싣기, 재접속에서 과거 Overlay·GuideProposal 재생.
- FIXTURE 테스트 통과를 실기기 PASS로 기록.

## 막힌 것

- 이슈 #3의 실제 앱별 프로필과 screen_revision 규칙은 AVD에서 확인해야 한다.
- 이슈 #1 Android 골격 전: 클라이언트 쪽 N-13 처리의 실제 구현·검증 불가.
