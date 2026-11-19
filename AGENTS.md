# AGENTS.md — Hyojason (효자손)

코딩 에이전트(Codex, Claude Code 등)와 팀원이 공통으로 따르는 작업 규칙 요약이다. 세부 계약은 `docs/`가 기준이며, 이 문서와 다르면 `docs/`가 맞다. 모르는 것은 추측하지 말고 이슈나 `docs/work/` 질문 문서에 적는다.

## 프로젝트

자녀가 통화로 말한 버튼을 부모가 3초 안에 누르지 않으면 부모 화면에 빨간 테두리로 짚어 주는 Android Agent. 부모·자녀 두 역할이 같은 Kotlin APK를 쓰고, 음성은 LiveKit, 제어는 세션 서버, 판단은 Zoro-1(L40S)이 맡는다. 모드는 `CALL_CHILD_LED`(통화 중 3초 게이트)와 `POST_CALL_AGENT`(통화 후 이어받기). 개요는 `README.md`, `docs/architecture.md`.

## 담당 경로 (슬롯 A–D)

| 슬롯 | 담당(GitHub) | Big Node | 주 경로 |
|---|---|---|---|
| A | Nonmaju | B-03 화면 관찰, B-06 부모 안내 | `android/observer`, `android/overlay` |
| B | Bellashin | B-01 연결·통화, B-02 발화 | `android/rtc`, `android/child` |
| C | Jungminii-1114 | B-05 판단, B-08 모델·GPU | `server/agent` |
| D | wnddnr0914 | B-04 도움 상태, B-07 피드백·종료, B-08 통합 | `server/session`, `demo/fixtures` |

- 자기 경로가 아닌 파일은 수정하지 않는다. 필요하면 담당자에게 이슈나 PR 코멘트로 요청한다.
- 공통 계약(`docs/contracts.md`, `docs/node-result-contract.md`)을 바꿔야 하면 영향받는 담당자끼리 먼저 합의한 뒤 PR을 낸다. 구현이 계약과 어긋나면 구현을 고친다.
- 작업 배분과 일정은 `docs/work-plan.md`.

## 반드시 읽을 문서

1. `docs/contracts.md`, `docs/node-result-contract.md`, `docs/instruction-policy.md`
2. 자기 Big Node(`docs/big-nodes/B-0x.md`)와 하위 Small Node(`docs/small-nodes/N-xx.md`)
3. `docs/verification-plan.md`(실험·회귀 케이스), `docs/implementation-contract.md`(모듈 경계)

## 불변 계약 (어기면 안 되는 것)

- 모든 노드는 `NodeResult{status, outcome, data, diagnostics}`를 반환한다. 오류는 `outcome=null`, `data=null`. 진단 `basis`의 필수 키 11개를 채운다.
- **프라이버시:** 화면 원본, 좌표, OCR 본문은 부모 기기 밖(서버·자녀·로그)으로 보내거나 디스크에 영속 저장하지 않는다. 서버로 가는 것은 `ScreenState`(screen_kind, target_ids, evidence_codes)뿐이다. 자녀에게는 도착 추정과 완료 상태만 간다.
- **3초 게이트:** 통화 중 t0는 부모가 `instruction.accepted`를 처음 적용한 단조 시각이다. 같은 지시 재수신, 위치 탐색, 프레임, 부모 발화는 t0를 바꾸지 않는다. 정확히 3000ms에 입력 증거가 같이 오면 증거를 먼저 처리한다. 새 지시나 모드 전환 시 이전 타이머·표시·제안은 즉시 폐기한다. 우선순위는 `docs/instruction-policy.md`.
- **금지:** 자동 클릭·입력·발송, 전역 터치 수집, 화면 영상 트랙 전송, 시스템 터치 보안 우회.
- 위험·되돌리기 어려운 동작은 지정 문구(`되돌리기 어려운 동작이에요. 천천히 확인하세요`)만 표시한다.
- 오래된 epoch/revision의 결과로 표시하거나 상태를 바꾸지 않는다(`STALE_BASIS`).

## 검증과 기록

- 결과는 `source`(`LIVE`/`FIXTURE`)를 구분하고, 모의·fixture 통과를 라이브 성공으로 세지 않는다.
- 필요한 증거를 못 얻으면 `UNVERIFIED`로 남긴다. 완료·완주 수에 넣지 않는다.
- 실기기·AVD·실모델이 필요한 항목은 에이전트가 단독으로 PASS를 선언하지 않고 사람의 확인을 요청한다.
- 케이스 ID(`docs/verification-plan.md`, 각 노드 문서의 테스트 표)를 테스트 이름과 커밋 메시지에 쓴다.
- Android 테스트·빌드는 `android/`에서 실행한다(JDK 17+, Android SDK Platform 35, Build Tools 35.0.0). 자세한 설정은 `android/README.md`.

```powershell
.\gradlew.bat :contract:test :observer:testDebugUnitTest :overlay:testDebugUnitTest :rtc:testDebugUnitTest :child:testDebugUnitTest :app:assembleDebug
```

- Windows에서 프로젝트·SDK 경로에 한글이 있으면 빌드가 실패할 수 있다. 사용 중이지 않은 드라이브 문자에 `subst`로 레포 상위 폴더를 연결한 뒤 그 경로에서 실행하고, `ANDROID_HOME`도 그 드라이브 기준으로 맞춘다.

## Git·PR 규칙

- 각자 슬롯 브랜치(`A`/`B`/`C`/`D`)에서 작업하고 `main`에 직접 푸시하지 않는다. 브랜치 체계는 팀 합의로 바뀔 수 있다.
- 커밋은 검증이 끝난 단위마다 하고, 메시지 본문에 판단 이유를 1~3줄 적는다(왜 이 방식인지, 버린 대안·한계). 수치·결정은 케이스 ID나 이슈 번호와 함께 쓴다.
- 푸시, PR, 이슈 생성, 머지는 사용자(담당자) 확인 후에만 한다. 머지는 사람이 한다.
- 계약·공통 타입을 건드리는 PR은 영향받는 담당자가 리뷰한다.
- 공개 레포에 실계정, 토큰, 키, 캡처 원본, 실제 서버 주소를 올리지 않는다. `.env.example`에는 가짜 값만 둔다.

## 에이전트 협업

- 한 worktree는 한 번에 한 도구만 수정한다. 작업 문서 맨 위에 `현재 담당: <도구> (날짜)` 한 줄을 둔다.
- 작업을 멈추거나 넘길 때 작업 문서의 현재 상태, 다음 할 일, 하면 안 되는 일, 막힌 것을 갱신하고 커밋까지 끝낸다.
- 에이전트별 기억(메모리·스킬)은 서로 공유되지 않는다. 다른 도구가 알아야 할 내용은 레포 문서에 적는다.

## 열려 있는 결정 (이슈)

- #3 screen_kind 값 목록·의미 전이·target_id 안정성 (A↔D)
- #5 N-24·N-25 라이브 리허설 증거 연동 (B-08)
- #6 통화 발신 방향과 안내 대상(`assisted_role`) 분리
