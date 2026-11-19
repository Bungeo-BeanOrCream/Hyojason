# 구현 계약 · 자녀 지시와 통화 후 이어받기

[전체 구조](architecture.md) · [NodeResult와 Diagnostic](node-result-contract.md) · [실험과 기대 결과](verification-plan.md)

## 스코프와 값의 소유자

| 값 | 작성자 | 유효 범위와 변경 |
|---|---|---|
| pair_id, call_id, help_id | 제어 서버 | 승인된 가족 관계, 통화 시도, 도움 1회를 각각 식별. 통화 종료 뒤 같은 help_id가 생존할 수 있다 |
| event_id, event_seq | 발행자, 제어 서버 | event_id는 중복 제거, event_seq는 도움별 서버 처리 순서. 기기들의 벽시계로 정렬하지 않는다 |
| consent_revision | 제어 서버 | 승인 상태가 바뀌면 증가. 동의 철회는 이미 준비된 결괏값도 무효화한다 |
| mode, mode_revision | N-11 | CALL_CHILD_LED 또는 POST_CALL_AGENT. 실제 hangup 확정에서만 전환하고 revision 증가. 일시적 RTC 재연결은 대기 |
| utterance_id, utterance_seq | N-06 | call_id의 확정 발화 식별. 같은 turn은 한 번 채택한다. 늦은 응답으로 새 자녀 발화를 덮지 않는다 |
| instruction_id, instruction_revision | N-11 | CALL_CHILD_LED에서 채택한 최신 자녀 지시 하나. 새 지시마다 revision 증가. 모호하지만 분명히 새 지시인 턴은 이전 표시를 먼저 무효화 |
| goal_revision | N-11 | 유효한 명시적 목표/슬롯을 채택했을 때만 증가. 변경 감지 질문 없이 최신 자녀의 명시적 목표 결과를 적용 |
| projection_epoch, screen_revision, layout_revision | 부모 AVD | 캡처 재승인, 안정 의미 전이, 위치가 달라지는 레이아웃 전이를 구분. 스크롤만으로 통화 중 타이머를 재시작하지 않는다 |
| decision_id, arrival_id | 서버 | 제안·도착의 중복 제거 키. Basis와 함께 전달 |

Basis의 필드와 결과 적용 규칙은 [공통 결과 계약](node-result-contract.md)을 따른다. `event_seq`는 인과 추적용이며 관련 버전 전체가 최신이면 서로 무관한 이벤트의 seq 증가만으로 결과를 버리지 않는다.

## 자료형

아래 키는 필수다. `?`는 null을 허용한다. ID는 승인된 스코프 안의 문자열이며 uint는 0 이상 정수다.

| 타입 | 필드 | 유효성 및 범위 |
|---|---|---|
| FinalUtterance | utterance_id, utterance_seq, speaker_role, text, call_id, source | text는 세션 처리에 필요한 동안 휘발성으로만 사용. role은 LiveKit 트랙 소속에서 결정 |
| InstructionDraft | source_utterance_id, target_key?, action, supersedes_previous | N-15의 권위 없는 기본 지시. N-11이 instruction_id와 revision을 발급 |
| Instruction | instruction_id, instruction_revision, source_utterance_id, target_key?, action, origin_screen_revision? | P0은 최신 지시 하나, 허용 라벨/행동으로 정규화. 목록·원문은 자녀 화면에 전송하지 않음 |
| GoalDraft | goal_id, approved_slots, source_utterance_id | 모델을 통과한 목표·슬롯. revision은 reducer만 발급 |
| ScreenState | projection_epoch, screen_revision, screen_kind, target_ids[], evidence_codes[], observation_health | 부모 화면 분류·검증 증거만 전송. 본문, 좌표, 일반 OCR 텍스트 제외 |
| LocalObservation | screen: ScreenState, layout_revision, captured_mono_ms, targets[] | 부모 프로세스 전용. targets는 target_id·Rect·검증 근거. 캡처가 멈추면 참조 폐기 |
| Goal | goal_id, goal_revision, approved_slots, source_utterance_id | 지원 목표 카탈로그만 허용. 발화자가 자녀라고 해서 금지 동작을 허용하지 않음 |
| TrailEntry | from_screen, to_screen, projection_epoch, evidence_codes[], reversible, observed_seq | 검증된 두 화면 사이 전이. 공백은 별도 marker, 잘못 누름 복귀와 통화 후 이력 검색에 사용 |
| InstructionTrace | instruction_id, instruction_revision, target_id?, origin_screen_revision?, evidence_codes[], outcome | 최신 수락 지시·위치·수행 증거의 이력 요약. 원문 또는 좌표 미포함 |
| NavigationTrail | entries[], directives[], gap_after_seq? | 최근 동일 도움의 검증 전이·지시 증거와 공백 위치 |
| Candidate | choice_id, action, target_id?, label_key, evidence_codes[], path_distance? | 행동 하나를 표현. action은 TAP, BACK, SCROLL_DOWN, SCROLL_UP, INPUT_HINT, WAIT 중 하나. 좌표·실행 명령 제외 |
| CandidateSet | candidates[], coverage, active_count, recovery_count | coverage는 complete, partial, policy_blocked. 배열 수와 계수 일치. [N-16](small-nodes/N-16.md) |
| TargetDecision | location_kind, target_id?, anchor_screen_revision?, evidence_codes[], model_revision? | visible, below_fold, unknown, blocked. below_fold는 프로필과 현재 컨테이너 증거 모두 필요 |
| ActionVerdict | verdict, instruction_revision, from_screen_revision, to_screen_revision, evidence_codes[], model_revision? | CORRECT, WRONG, UNKNOWN. NO_OBSERVED_INPUT은 발화 뒤 관찰이 연속적이고 변화 단서가 없는 N-19의 로컬 게이트 값 |
| RecoveryHint | instruction_revision, anchor_screen_revision, first_edge_ref, path_distance, basis | N-16의 검증 역방향 경로 요약. ActionVerdict가 WRONG이고 최신 효과 버전과 일치할 때만 사용 |
| GuideProposal | decision_id, mode, action, target_id?, message_key, confidence, valid_for_ms, basis | CALL_CHILD_LED는 정확한 지시·입력 근거 기반의 TAP/BACK/SCROLL_DOWN만. POST_CALL_AGENT는 현재 한 행동. 둘 다 실제 클릭 명령 금지 |
| ArrivalDecision | arrival_id, arrived, goal_revision, evidence_codes[], basis | POST_CALL_AGENT에서 화면 증거가 검증된 경우에만. 자녀의 완료 명령과 분리 |
| HelpSnapshot | help_id, call_state, help_state, mode, mode_revision, goal?, instruction?, screen?, trail_cursor, consent_revision, event_seq | N-11 원본 상태. 부모·자녀에게는 N-21이 필드별 권한 검사 후 투영 |

### 두 판단의 결합

N-15의 지시 해석은 스스로 성공했으면 목표 추론 완료를 기다리지 않고 B-05가 N-11에 초안을 보낸다. 같은 발화의 목표가 준비되면 B-05가 별도 목표 초안을 보낸다. 서버는 (utterance_id, 초안 유형)으로 중복을 막고, 최신 자녀 발화에만 두 종류를 허용한다. 최종 N-15 결과는 한 실행의 기록이며 초안을 다시 발행하지 않는다. 버튼을 정확히 탭했는지 확인할 때는 다른 실패나 늦은 목표 결과로 지시의 t0를 바꾸지 않는다.

## 이벤트와 전송

REST는 인증·페어·토큰·재연결 조회, TLS WebSocket은 역할 제어 이벤트에 쓴다. LiveKit은 음성 전송에 쓴다. 모든 제어 이벤트는 `v, type, event_id, pair_id, call_id, help_id, payload`를 갖는다. help/call을 아직 만들지 않았으면 해당 ID는 null이다. 서버는 인증 토큰에서 actor를 얻고, 도움·통화·역할·선행 권한을 검사한다.

| 이벤트 | 발행 → 수신 | payload 및 효과 |
|---|---|---|
| call.invite / accepted / ended | 각 역할·RTC → N-11 | 역할, 사유, call_id. ended에서 STT 닫기와 Agent 이어받기 평가 |
| consent.updated | 해당 역할 → N-11 | purpose, enabled, consent_revision. 철회에서 표시·동의 범위 정리 |
| speech.final | N-06 → N-11/N-15 | FinalUtterance. 부모 발화는 통화 중 계측만 하며 지시·목표·타이머를 만들지 않음 |
| instruction.accepted / cleared | N-11 → 부모 게이트 | 정규화된 Instruction 또는 취소 사유. 최초 수신으로만 부모 시각을 설정 |
| goal.updated | N-11 → 부모·내부 | Goal. 자녀에게 목표·지시·단계 진행 투영 없음 |
| screen.observed | 부모 → N-11 | ScreenState. 보호/관찰 중단은 건강 상태만 전송 |
| target.located | N-27 → N-19, N-11 | TargetDecision과 Basis. 캡처와 지시 스코프 유지 |
| recovery.ready | N-16 → N-19 | RecoveryHint와 최신 Basis. 옛 화면 좌표 없이 경로 첫 행동만 |
| action.assessed | N-28 → N-19, N-11 | ActionVerdict와 Basis. 입력 시각의 관측 근거와 스코프를 보존 |
| guide.proposed / cleared | N-17 또는 B-05 → 부모 | GuideProposal 또는 폐기 이유. 표시 여부는 N-19가 로컬 재검사 |
| guide.shown / suppressed | 부모 → N-11 | decision_id, instruction/goal/mode/screen revision, elapsed_ms, 사유. 화면 내용은 제외 |
| arrival.suspected / revoked | N-22 → 자녀 | arrival_id, 도착 추정 여부. 원문 및 세부 진행 이력 제외 |
| help.complete / stop | 해당 자녀 / 부모 → N-22 | help_id, 종료 이유, 최신 스코프. 같은 명령은 한 번만 적용 |
| help.ended | N-23 → 양쪽 | end_event_id, reason. 도움 캐시 정리. 완료는 부모 완료 문구 |

## 통화 중 3초 게이트

1. 자녀의 지시를 정규화해 부모 앱이 `instruction.accepted`를 처음 적용했을 때의 단조 시각을 `t0`로 잡는다. deadline = t0 + 3000ms. 위치 결정, 네트워크 재전송, 프레임 수신, 부모 발화는 시작점이 아니다. `발화 종료 → t0`는 별도 지연으로 보고한다.
2. 위치 탐색은 t0 전후로 병렬 진행한다. 다른 지시가 오면 이전 타이머·표시·후보를 즉시 폐기하고 새 t0만 세운다. 같은 instruction_id 재수신은 재예약하지 않는다.
3. 마감까지 맞는 행동 증거가 있으면 표시 0회, 진행 단위는 끝난다. 마감과 동일 시각의 증거는 표시보다 먼저 처리한다. `관측 됐으나 판정 대기` 상태에서는 렌더를 보류한다. 뒤늦게 받은 과거 근거는 표시 후 즉시 제거하되 타이밍 실험에서 과거 오표시를 성공 처리하지 않는다.
4. 마감부터 연속 관찰에서 조작 단서가 없고 화면이 유효하면, 보이는 정확한 버튼은 TAP 표시, 아래 위치가 입증되면 스크롤 문구. 지시와 다른 결과가 검증되고 원래 화면으로 검증된 역엣지가 있을 때 BACK 문구를 표시한다. 충돌 증거·캡처 공백·무효 레이아웃·판정 대기에서는 테두리를 띄우지 않는다.
5. 앱은 전역 터치를 수집하지 않는다. 외부 앱 화면의 전이·상태 차이에서만 조작 결과를 확인한다. 프레임만으로 알 수 없는 입력과 위치는 `UNKNOWN`이며 연속 관찰의 무변화와 나누어 기록한다.
6. 위험은 표시 게이트 밖의 보호 문구만 허용한다. **되돌리기 어려운 동작이에요. 천천히 확인하세요**. 자동 클릭·입력·발송은 금지한다.

## 통화 후 3초 게이트

확정된 hangup 뒤 N-11이 `POST_CALL_AGENT`로 한 번 전환한다. 유효 목표가 있으면 최신 부모 화면을 재확인하고, 전환 시 첫 안정 화면이나 이후 의미 화면 전이를 부모가 관측한 시각부터 3,000ms 뒤에 한 행동을 제안할 수 있다. 로컬에서 적용한 mode_revision과 goal_revision이 같아야 한다. 지난 지시의 타이머와 응답은 이전 모드에 묶여 폐기된다. Zoro-1의 `NEXT_ACTION`과 `ARRIVAL`은 이 모드에서만 호출한다. 검증된 현재 선택이 없으면 기다린다. 도착 추정은 자녀에게 보내고 최종 종료는 자녀가 확정하거나 부모가 안내를 끈다.

## 공통 구현값

| 항목 | 초기 계약 |
|---|---|
| 안정 관측 | 같은 의미 화면이 연속 두 번 확인될 때 안정 처리. 초기 표본 속도 2–4fps. 프레임 최신성 기준 1초, 실제 장치 실험으로 조정 |
| 좌표 표시 | 화면·모드·목표/지시·동의·epoch 재검사. layout_revision과 로컬 좌표 재매칭. 오래된 UI와 보호 화면은 제거 |
| 제안 만료 | 수신 시점부터 valid_for_ms. 만료는 새 입력 조건이 될 수 있으나 같은 Basis를 반복 추론하지 않는다 |
| 모델 기준 | 시작값 top ≥0.70, top−second ≥0.15. 고정 revision과 검증된 후보 ID. 보류·모델 오류를 분리 |
| 현재와 이전 결과 | 검증된 상태 변경만 N-11에 커밋. 이전 노드는 새 이벤트와 참조로 읽는다. 원문·원본·좌표 영속 로그 없음 |
