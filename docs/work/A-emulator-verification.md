# A 슬롯 에뮬레이터 검증 체크리스트와 검증 후 작업

현재 담당: Claude (2026-11-19)

에뮬레이터 확인은 사람이 Mac에서 한다. 에이전트는 아래 항목에 단독으로 PASS를 쓰지 않는다. 사람이 확인한 결과만 `UNVERIFIED`에서 바꾼다. 결과에는 이미지를 **"36.1 Google APIs x86_64(16KB 아님)"**로 적는다. Android 15(API 35) 결과가 아니다.

## 1. 에뮬레이터 없이 끝난 것 (JVM 테스트 통과)

- B-03: N-07 세션·epoch·STALE_BASIS(`ProjectionSessionTest`), N-08/N-09/N-10 탐지·안정화(`FrameDetectionTest`, `ScreenStabilizerTest`).
- B-06: N-19 게이트(`GuideGateTest`), N-20 표시 판단(`ParentSurfacesTest` 20.1~20.5), N-19→N-20 호출부 `ParentGuideFlow`(`ParentGuideFlowTest` 6건: B06.1~B06.3, 정책 거부, 구 epoch·help_id 폐기).
- 위 통과는 `source=FIXTURE`다. 라이브 성공으로 세지 않는다.
- 일부러 만들지 않은 것: "도움 종료" 하나로 캡처 중지·표시·타이머·제안을 묶는 진입점(#6 합의 전), 끄기/통화로 → N-22 연결(B/D 경로).

## 2. Mac 준비

1. Android Studio 설치, SDK Platform 35와 Build Tools 35.0.0 설치(앱은 minSdk/targetSdk 35).
2. Device Manager에서 에뮬레이터 생성: Google APIs, x86_64, API 36.1(Apple Silicon Mac이면 arm64 이미지). 앱이 API 35 이상을 요구하므로 36.1도 설치된다.
3. 레포를 받아 `android/`를 Android Studio로 연다. 브랜치는 `A-rebased`(아직 푸시 전이면 사용자가 전달한 방식으로 받는다).
4. 실행: `app` 구성을 에뮬레이터에 Run, 또는 터미널에서 아래. 런처 이름은 "AVD 검증", 앱 ID는 `com.hyojason.app.debug`.

```bash
cd android
./gradlew :app:installDebug
```

5. 캡처 단독 확인용 APK는 별도 폴더 `N07-probe`(레포 밖)에 있다. Mac에는 없으니 필요하면 Windows에서 가져오거나 `N07-probe`를 빌드한다.

## 3. 사람이 확인할 항목 (결과를 기록해 달라)

각 항목 결과는 PASS/FAIL과 한 줄 관찰을 적는다. 화면 캡처 이미지는 레포에 올리지 않는다.

### 오버레이 권한·테두리 (N-20, B06.4)

| # | 확인 | 기대 결과 |
|---|---|---|
| E1 | "AVD 검증" → 오버레이 권한 설정에서 허용 | 앱 복귀 시 권한 허용 상태 표시 |
| E2 | "테스트 테두리 표시" | 빨간 테두리가 보인다 |
| E3 | 테두리가 떠 있는 상태로 Settings, Clock 같은 다른 앱을 누르고 스크롤 | 테두리 아래 앱이 터치를 그대로 받는다(터치 통과) |
| E4 | "테스트 테두리 제거" | 테두리가 사라진다 |
| E5 | 테두리 표시 중 오버레이 권한을 끈다 | 창이 사라지고 앱이 죽지 않는다 |
| E6 | 두 번째 검증 화면을 열어도 | 테두리는 하나만 유지된다 |

참고: 검증 화면의 테스트 테두리는 별도 창이고 `AndroidSurfaceHost`(띠의 끄기·통화로 버튼 포함)를 쓰지 않는다. 띠 버튼과 `ParentGuideFlow` 결합은 debug 진입점이 생기기 전에는 에뮬레이터에서 확인할 수 없다(후속 작업 4).

### 화면 공유 (N-07, 07.1~07.5)

| # | 확인 | 기대 결과 |
|---|---|---|
| E7 | 화면 공유 시작 → 시스템 대화상자에서 "전체 화면" 허용 | epoch가 표시되고 프레임 수가 증가한다 |
| E8 | 화면 공유 시작 → 거절 | 프레임 소스 없음, 프레임 수 0 |
| E9 | "사용한 승인 다시 제출" | 결과 `DUPLICATE`, 기존 epoch 유지 |
| E10 | 중지 후 다시 허용 | 이전과 다른 새 epoch, 이전 epoch 프레임은 폐기(`STALE_BASIS`) |
| E11 | 시스템의 공유 중지(상태바 알림) | 프레임 수신 멈춤, 로컬 관찰 폐기 |
| E12 | 앱 종료 또는 권한 철회 | 수신이 멈춘다 |

- 확인 사실 기록: 프레임은 화면에 표시하지 않는다. 숫자(epoch, 프레임 수)만 본다.
- Android 15에서 Settings → Clock 전환을 확인할 때는 전체 화면 공유를 고른다.

### 보류 항목 (이번 Mac 검증 범위 밖)

- N-08 프로필 매칭, N-09 한국어 OCR, 보호 화면 판정의 실화면 확인: 현재 debug 화면에 연결된 진입점이 없다. Settings 대상 인식이 필요하면 후속 작업 4에서 진입점을 만든 뒤 확인한다.
- 통화 종료 후 같은 도움에서 관찰 지속(07.x 실통화): B의 통합 APK가 필요하다.

## 4. 검증 후 해야 할 일

1. **결과 반영**: 사람이 준 결과를 `docs/work/A-open-questions.md`의 N-07·N-20·B-06 항목에 옮기고 `UNVERIFIED`를 바꾼다. 36.1 결과임을 같이 적는다. 실패 항목은 원인 조사 후 구현을 고친다(문서를 맞추지 않는다).
2. **Android 15 재확인**: API 35 이미지가 가능한 환경이 생기면 E1~E12를 한 번 더 한다. 그 전에는 "Android 15 확인 완료"라고 쓰지 않는다.
3. **브랜치 정리**: 로컬 `A`(옛 기반 09d714b)와 `A-rebased`(origin/main c3f3a84 위)의 정리 방식을 사용자가 정한다. 푸시와 PR은 사용자가 확인한 뒤에만 한다. PR은 Big Node 하나가 끝날 때 하나씩 올린다(B-03, B-06).
4. **debug 진입점(선택)**: `:app`(B와 공유 경로)에 `AndroidSurfaceHost` + `ParentGuideFlow`를 호출하는 debug 화면을 추가하면 띠 버튼과 Settings 대상 안내를 에뮬레이터에서 볼 수 있다. `:app`은 A 경로가 아니므로 B와 합의 후에 한다.
5. **N-22 연결**: `SurfaceUserEvent.DISMISS_GUIDE/RETURN_TO_CALL`을 서버 이벤트로 보내는 부분은 B/D와 합의 후에 한다.
6. **`trace_id` 발급 주체**: D의 답을 받아 `HelpTraceIds.inject` 호출 위치를 정한다(이슈 #5).
7. **#6 합의 후**: 연결 식별자와 `call_id` 비교(`GuideGate.agrees`) 정리, "도움 종료" 묶음 진입점, 이름 변경.
8. **문서 정리**: `A-codex-brief.md`(레포 밖) 89행 부근의 오래된 N-20 설명 갱신.

## 5. 기록 규칙

- 모의·fixture 통과를 라이브로 세지 않는다. 에뮬레이터 결과는 `source=LIVE`가 아니라 "AVD 36.1 확인"으로 구분해 적는다(실기기 확인과 구분).
- 증거를 못 얻은 항목은 `UNVERIFIED`로 둔다.
- claude-mem이 로그아웃 상태(2026-11-19)라 기억 저장이 안 된다. 이 문서와 `A-open-questions.md`가 인계 기록이다.
