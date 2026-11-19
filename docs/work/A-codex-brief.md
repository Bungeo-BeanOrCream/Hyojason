# A 슬롯 구현 지시서 (Codex용): B-03 화면 관찰 · B-06 부모 안내

현재 담당: Codex (2026-11-19 시작)
브랜치: `A` (레포 Bungeo-BeanOrCream/Hyojason). `main`에 직접 커밋·푸시 금지.

이 문서는 Codex가 읽고 바로 구현을 시작하기 위한 지시서다. 사실의 근거는 레포 `docs/`이며, 이 문서와 `docs/`가 다르면 `docs/`가 맞다(그 경우 구현을 멈추고 `docs/work/A-open-questions.md`에 적는다).

## 1. 무엇을 만드는가

Android(Kotlin) 부모 기기용 모듈 두 개. 제안 경로는 `docs/implementation-contract.md` 기준이다.

| 모듈 | 노드 | 책임 |
|---|---|---|
| `android/observer` | N-07 projection 열기/닫기, N-08 프레임 정규화, N-09 화면·대상 검출, N-10 화면 전이 확정 | MediaProjection 캡처, 보호 화면 선분류, `ScreenState`/`LocalObservation` 발행 |
| `android/overlay` | N-19 3초 게이트, N-20 부모 Overlay 렌더 | 3초 판정, 빨간 테두리·상단 띠, 터치 통과 |

팀에 넘길 결과: Settings·Clock 실화면에서 버튼을 찾아 빨간 테두리를 그리고, 그 아래 버튼이 실제로 눌리는 것.

## 2. 먼저 읽을 문서 (이 순서)

1. `docs/contracts.md`, `docs/node-result-contract.md`, `docs/instruction-policy.md`
2. `docs/big-nodes/B-03.md`, `docs/big-nodes/B-06.md`
3. `docs/small-nodes/N-07.md` ~ `N-10.md`, `N-19.md`, `N-20.md`
4. `docs/verification-plan.md` (V-04, E04~E06, E08, E09, E11, E14), `docs/implementation-contract.md`

## 3. 구현 순서와 완료 기준

각 단계는 테스트가 통과해야 다음으로 간다. 한 단계 = 커밋 한 번 이상.

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 0 | `android/` Gradle 골격(없으면). 별도 첫 커밋으로 분리 | 빈 모듈 빌드 성공. 기존 골격이 있으면 그것을 따른다 |
| 1 | 공통 타입: `NodeResult`, `Diagnostic`, `Basis`(필수 키 11개), 상태 enum | 계약 문서의 필드·null 허용과 1:1 일치하는 단위 테스트 |
| 2 | **N-19 순수 로직** (Android 의존 없음, `now_mono_ms`·`t0_mono_ms` 주입) | 테스트 19.1~19.9, B06.1~B06.3 통과. 2999/3000/3001ms 경계 포함 |
| 3 | N-10 안정화 로직 (순수) | B03.1: 같은 의미 화면 연속 2회 후 스크롤은 `layout_revision`만 증가 |
| 4 | N-08, N-09 (fixture 프레임 기반) | 09.1~09.5, B03.3, B03.4(오버레이 마스크) |
| 5 | N-07 MediaProjection | 07.1~07.5. 새 승인마다 새 `projection_epoch`, 구 epoch 프레임은 `STALE_BASIS` |
| 6 | N-20 Overlay | 20.1~20.5. `TYPE_APPLICATION_OVERLAY` + `FLAG_NOT_TOUCHABLE` |
| 7 | 실기기 검증 (사람 몫) | B06.4: Settings/Clock에서 테두리 아래 버튼 실제 터치 성공 |

단계 2~4는 AVD 없이 JVM 단위 테스트로 끝나야 한다(순수 Kotlin 패키지로 분리). 단계 5~7은 AVD가 필요하며 Codex가 단독으로 PASS를 선언하지 않는다. 결과는 `UNVERIFIED`로 남기고 사용자에게 실기 확인을 요청한다.

## 4. 반드시 지킬 계약

**3초 게이트 (N-19)**
- 통화 중 t0는 부모 앱이 `instruction.accepted`를 처음 적용한 단조 시각이다. 재전송, 위치 탐색, 프레임 수신, 부모 발화는 t0를 바꾸지 않는다. 같은 `instruction_id`는 재예약하지 않는다.
- 정확히 3000ms에 입력 증거가 같이 오면 증거를 먼저 처리한다. 판정 대기 중에는 렌더를 보류한다.
- 우선순위: suppressed(철회·보호·오래된 상태) → policy_denied → followed(표시 0회) → waiting → BACK(오선택 + 검증된 역엣지) → SCROLL_DOWN(below_fold 증거) → TAP → 그 외 waiting. 세부는 `docs/instruction-policy.md`.
- 통화 후 t0는 모드 전환 뒤 첫 안정 의미 화면이다. 새 지시나 모드 전환 시 이전 타이머·표시·제안은 즉시 폐기한다(B06.3).
- 위험 문구는 정확히 `되돌리기 어려운 동작이에요. 천천히 확인하세요`.

**관찰 (B-03)**
- 같은 의미 화면 연속 2회에서 안정으로 확정. 스크롤·키보드는 `layout_revision`만, 의미 변화만 `screen_revision`을 올린다.
- 통화 종료만으로 캡처를 멈추지 않는다. 도움 종료, 권한 철회, `onStop`에서만 멈추고 프레임·좌표를 폐기한다.
- 자기 Overlay가 찍힌 영역은 검출·도착 단서에서 제외한다.
- 좌표(`LocalObservation`, `LocalTarget`)는 부모 기기 밖으로 나가지 않는다. 서버 쪽으로 가는 건 `ScreenState`(target ID·근거 코드)뿐이다.

**결과 봉투**
- 모든 노드는 `NodeResult{status, outcome, data, diagnostics}`. `error`일 때 `outcome=null`, `data=null`.
- 진단에 사용자 원문, 좌표, 스택트레이스, 토큰을 넣지 않는다. `source`는 `LIVE`와 `FIXTURE`를 구분한다.

## 5. 하지 말 것

- 자동 클릭·입력·발송, 전역 터치 수집, 영상 트랙 전송 금지.
- 프레임 원본·좌표·OCR 본문을 서버로 보내거나 디스크에 영속 저장하지 않는다(메모리 전용).
- 실계정, 토큰, 캡처 원본, 서버 주소를 레포에 올리지 않는다(`.env.example`에는 가짜 값만).
- 모의(fixture) 통과를 LIVE 성공으로 기록하지 않는다. 증거를 못 얻으면 `UNVERIFIED`.
- `docs/` 계약 문서를 임의로 수정하지 않는다. 계약 변경이 필요하면 `docs/work/A-open-questions.md`에 적고 멈춘다.
- B/C/D 담당 경로(`android/rtc`, `android/child`, `server/*`)를 수정하지 않는다.
- 계약 문서에 없는 동작은 추측하지 않는다. 적고 질문한다.

## 6. 작업 규칙

- 한 worktree는 한 번에 한 도구만 수정한다. 이 문서 맨 위의 `현재 담당` 줄을 갱신한다.
- 커밋은 검증이 끝난 단위마다 하고, 메시지 본문에 판단 이유를 1~3줄 적는다(왜 이 방식인지, 버린 대안·한계). 테스트 ID(예: 19.9, B06.1)를 함께 쓴다. AI 공저자 표기는 넣지 않는다.
- 푸시와 PR은 사용자 확인 후에만 한다. 머지는 사용자만 한다.
- 계약·공통 타입에 닿는 PR은 Claude가 리뷰한다. 작은 PR은 사용자가 1차 확인한다.
- 작업을 멈출 때는 이 문서 하단 "진행 상태"를 갱신한다: 현재 상태, 다음 할 일, 하면 안 되는 일, 막힌 것. 그리고 커밋까지 끝낸다.

## 7. 임시 결정 (Codex는 이대로 진행, 바뀌면 사용자가 알린다)

- 언어 Kotlin, 빌드 Gradle(Kotlin DSL), 대상 Android 15(API 35), 단위 테스트 JUnit + JVM.
- 순수 로직(N-19, N-10)은 Android 의존 없는 패키지에 둔다.
- 공통 타입은 `android/observer`의 `contract` 패키지에 문서와 1:1로 둔다. B/D와 공유 모듈이 정해지면 옮긴다.
- N-09 검출은 온디바이스(OCR·아이콘·배치 특징). 첫 구현은 fixture 프레임과 앱 프로필 registry 기반으로 하고, 실제 OCR 엔진 선택은 단계 4 시작 전에 사용자에게 확인한다.

## 진행 상태 (작업자가 갱신)

- 현재 상태: `Hyojason/`에 레포 복제, `ce7dc38`을 기준으로 로컬 `A` 브랜치 생성. Android 골격 없음. 구현 시작 전.
- 다음 할 일: N-19 결과 경계 확인 후 단계 0 Gradle 골격 빌드.
- 하면 안 되는 일: 위 5절
- 막힌 것: 오래된 `Basis`의 N-19 반환값 확인 필요. `Hyojason/docs/work/A-open-questions.md`에 쟁점 기록.
