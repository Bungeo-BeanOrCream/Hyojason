# 효자손 · B 담당 개발 정리 (LiveKit 통화 · 한국어 STT)

레포: https://github.com/Bungeo-BeanOrCream/Hyojason
먼저 읽을 문서: `docs/README.md` · `docs/work-plan.md` · `docs/big-nodes/B-01.md` · `docs/big-nodes/B-02.md` · `docs/small-nodes/N-01~N-06.md` · `docs/contracts.md` · `docs/node-result-contract.md` · `docs/implementation-contract.md`

> 상태: docs는 "설계 계약, 구현 검증 전". 함수명·경로는 제안이고 레포에 코드는 아직 없음.

---

## 1. 노드별 개발이란

- **Small Node(N-xx) = 함수 하나.** 문서마다 입력·출력 필드, 결과 상태, 테스트 표가 정해져 있음.
- 모든 노드는 같은 형식으로 반환: `NodeResult{status, outcome, data, diagnostics}`
  - `success` / `partial`: data 있음
  - `error`: `outcome=null`, `data=null`, `diagnostics.terminal_error`에 코드
- **Big Node(B-xx) = 함수들을 잇는 순서도.** 분기(예: 동의가 없으면 STT를 열지 않음)는 여기 있음.
- **진행 순서:** N-01부터 하나씩 완성하지 않는다. 실제 통화와 STT를 먼저 뚫고 → 노드 계약 모양으로 쪼개고 → 테스트를 붙인다.

## 2. B 담당 범위

| 노드 | 실행 위치 | 도구 | 요약 |
|---|---|---|---|
| N-01 resolve_pair | 서버 + Android 온보딩 | VS Code + Android Studio | 일회용 코드로 부모·자녀 1:1 페어 |
| N-02 resolve_consent | Android + 서버 | Android Studio + VS Code | AI 처리 동의와 OS 권한을 따로 계산 |
| N-03 start_or_close_call | Android + LiveKit + 서버 | Android Studio (+ 토큰 API는 VS Code) | 발신 → 수락 → RTC 연결/종료, 스피커 경로 1회 평가 |
| N-04 ingest_participant_audio | RTC 수신 서버 | VS Code | 승인된 마이크 트랙만 역할별로 받기 |
| N-05 transcribe_korean_turn | STT | VS Code | 한국어 부분/확정 인식 |
| N-06 finalize_instruction_turn | 서버 | VS Code | 중복·역순·종료 후 결과를 거르고 `speech.final` 1회 발행 |

**B의 최종 출력은 두 가지:** `call.*`(invite / accepted / ended), `speech.final`. 둘 다 D의 상태 서버(N-11)로 간다.

## 3. 개발 환경

- **저장소는 하나:** `android/`는 Android Studio, `server/`는 VS Code로 연다.
- **AVD 두 대:** Android Studio의 Device Manager로 실행.
- **에뮬레이터에서 Mac 로컬 서버 주소:** `localhost`가 아니라 `10.0.2.2`.
- **비밀값:** `.env`와 토큰은 커밋 금지. `.env.example`만 올린다.

## 4. 추천 구조 (추정, 팀 확정 필요)

```
[부모 AVD] ─mic─┐                          ┌─ STT stream (parent) ─┐
                ├─ LiveKit Room ─ STT worker ┤                       ├─ N-06 → speech.final → 세션 서버(D)
[자녀 AVD] ─mic─┘   (숨은 참가자)         └─ STT stream (child) ──┘
```

- **STT worker:** Python + LiveKit Agents. 룸에 참가자로 들어가서 participant identity로 역할을 매핑하고, 트랙마다 STT 스트림을 따로 연다. 참가자가 두 명이라 `AgentSession` 하나보다 트랙별 standalone 스트림을 검토한다(직접 확인 필요).
- **한국어 STT 후보(LiveKit 플러그인 있음):**
  - CLOVA: `ko-KR` 지원
  - RTZR: `sommers_ko` 모델, 키워드 부스팅 지원
  - 정확도는 미확인. 첫 1시간에 "내일 아침 6시", "아니, 화면 꺼지는 거부터"로 직접 비교한다.
- **팀이 정해야 할 것:** STT 업체, 서버 언어.

## 5. 시간대별 계획 (16.5시간 기준)

### 00:00–01:30 · 통화와 STT 뚫기 (다음 단계 진입 조건)
- [ ] LiveKit 프로젝트 생성, 임시 토큰 발급 스크립트 (처음엔 하드코딩 페어)
- [ ] 두 AVD 양방향 음성, 부모가 Settings로 전환해도 통화 유지 (마이크 foreground service)
- [ ] **가장 큰 위험:** Mac 마이크 하나가 두 AVD에 똑같이 들어가는 문제. 역할별 음소거, 장치 분리, 실기기 중 되는 방법을 찾아 문서화
- [ ] STT worker가 한 트랙을 받아 한국어 확정문을 콘솔에 출력
- [ ] D와 계약 고정: identity 규칙(예: `{pair_id}:{role}`), `speech.final` payload(`utterance_id, utterance_seq, speaker_role, text, call_id, source`), RTC가 끊겨도 제어 WebSocket 유지
- [ ] **텍스트 입력 스텁 제공** (`source=manual_test`로 `speech.final` 발행). C·D가 음성 없이도 통합을 시작할 수 있게.

### 01:30–05:00 · 시연 A(설정) 연결
- [ ] N-04: 역할 확인, call_id별 큐 분리, 영상이나 모르는 트랙은 무시
- [ ] N-05: 확정문만 다음으로 넘기고, 잡음·겹침은 `transcript_uncertain`
- [ ] N-06: `utterance_id` 중복 제거, 역순 폐기. 부모 발화로는 지시·목표·타이머를 만들지 않음(E07, D와 합의)
- [ ] "발화 종료 → speech.final" 지연 측정 (3초 게이트에 영향)

### 05:00–09:00 · 시연 B(통화 후 인계)
- [ ] N-03 끊기: `call.ended` 발행 → STT 취소 → 버퍼 폐기 → 같은 call_id로 늦게 온 확정문 폐기(B02.3)
- [ ] 도움 종료는 B가 하지 않음. `help.stop` 합성 금지
- [ ] 자녀 화면의 도착 추정·완료 버튼을 D(N-21/N-22)와 연동
- [ ] 첫 자녀 지시가 오면 스피커 전환 1회 평가. 이어폰·Bluetooth가 있으면 그대로 둠

### 09:00–12:00 · 엣지 케이스와 정식 기능
- [ ] N-01 일회용 초대 코드 (제3 단말 재사용 거부)
- [ ] N-02 동의 UI와 철회 처리
- [ ] 테스트: B01.1~4, B02.1~5, E07, E12
- [ ] STT timeout은 `no_speech`가 아니라 `error`

### 12:00–16:30 · 측정 → 동결 → 리허설
- [ ] 지시 20개 STT 정확도, 지연, STT 비용 기록. LIVE와 FIXTURE는 따로 기록
- [ ] 발표에서 B는 자녀 앱 담당

## 6. 노드 하나 만드는 방법

1. 노드 문서의 "상세 입출력 계약" 표를 타입(data class / pydantic)으로 옮긴다.
2. 함수가 `NodeResult`를 반환하게 한다.
3. "최소 검증" 표의 각 행을 테스트 케이스 하나로 만든다.
4. Big Node 순서도대로 연결한다.

N-06 예시 (스케치):

```python
def finalize_instruction_turn(t, active_call_id, seen_ids, last_seq):
    if t.call_id != active_call_id:            # 종료/철회 후 도착
        return error("STALE_BASIS")
    if not t.is_final:
        return success("partial_ignored", None)
    if t.utterance_id in seen_ids:
        return success("duplicate_ignored", None)
    if t.seq <= last_seq[t.role]:              # 역순
        return error("STALE_BASIS")
    seen_ids.add(t.utterance_id); last_seq[t.role] = t.seq
    return success("final_committed", FinalUtterance(...))
```

## 7. 서버 엔드포인트 (implementation-contract 제안)

| 메서드·경로 | 권한 | 처리 |
|---|---|---|
| `POST /v1/pairs/claim` | 두 역할 | 일회용 페어 승인 |
| `POST /v1/calls` | 부모 | 새 call_id, 자녀에게 수신 이벤트 |
| `POST /v1/calls/{call_id}/accept` | 해당 자녀 | 수락, 토큰 발급 경로 활성 |
| `POST /v1/calls/{call_id}/rtc-token` | pair 참가자 | 승인된 call/역할에만 LiveKit 토큰 |
| `WS /v1/events` | pair 참가자 | 제어 이벤트 (`v, type, event_id, pair_id, call_id, help_id, payload`) |

## 8. 확인이 필요한 부분

- `server/session` 코드를 B와 D 중 누가 짜는지 → 첫 회의에서 결정
- STT 업체, 서버 언어 → 팀 결정
- raw PCM과 인식 원문을 로그에 남기지 않기(B02.2). 디버그 print 주의.
