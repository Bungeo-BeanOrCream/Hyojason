# A 슬롯 구현 확인 사항

현재 담당: Claude (2026-11-19, Codex 5시간 제한으로 인계) — 에뮬레이터 확인 대기 중, 에이전트 작업 없음

## N-19에서 최신성 검증 실패 시 반환할 결과

- A 슬롯 브리프 4절과 [`instruction-policy.md`](../instruction-policy.md)는 이전 모드·지시·화면 등으로 현재 표시 조건을 충족하지 못하면 `suppressed`를 지정한다.
- [`N-19.md`](../small-nodes/N-19.md)의 반환 표와 19.8은 무효 스코프·권한·로컬 유효성 실패를 `error`로 지정한다. 이 경우 `outcome`과 `data`는 null이다. [`B-06.md`](../big-nodes/B-06.md)는 상위 결과에 `success/suppressed`를 정의한다.
- 2026-11-19 사용자 결정. 이전 제안의 `Basis`가 최신 상태와 다르면 N-19는 `error`와 `STALE_BASIS`를 반환한다. B-06은 기존 표시를 즉시 지우고 `success/suppressed`로 처리한다. 19.8과 B06.3에서 각각 검증한다.

## N-09 한국어 OCR 엔진

- 2026-11-19 사용자가 APK에 모델을 포함하는 [Google ML Kit 한국어 Text Recognition](https://developers.google.com/ml-kit/vision/text-recognition/v2/android?hl=ko)을 선택했다. 어댑터는 보호 검사와 픽셀 마스킹 이후 승인된 프로필 라벨만 반환한다.
- 이슈 #3 합의 화면·대상 ID와 상태 근거 코드를 합성 프레임 레지스트리에 반영했다. 프로필은 앱 버전과 AVD 이미지가 맞아야 적용하며, 같은 라벨의 대상은 레지스트리의 고정 `#<n>` ID를 쓴다. 앱별 라벨·아이콘·보호 화면 판별은 시연 기기에서 확인한다. 합성 프로필을 LIVE 결과로 적용하지 않는다.

## 이슈 #1~#3 통합 경계

- `2664f12`에서 `NodeResult`, `Diagnostic`, `Basis` 및 계약 테스트를 `android/contract`와 `com.hyojason.app.contract`로 옮겼다. D의 JSON Schema와 필드명·null 허용을 확인했다. 전송 직렬화에서는 상태의 `.code`와 필수 null 키를 보존한다.
- `e4dad9e`에서 N-10이 두 프레임으로 확인한 화면 종류나 승인 상태 근거가 달라질 때 화면 revision을 올리도록 했다. 스크롤에서 보이는 ID가 달라지면 동일 화면 revision으로 서버 요약을 갱신한다. 공백은 revision을 유지하고 첫 안정 화면은 새 관찰 구간을 시작한다. 이전 화면 revision은 연속 전이에만 제공한다.
- D 서버는 같은 revision의 가시 대상 갱신을 수락한다. 역경로·수행 판정에 쓸 전이 근거는 시연 환경에서 검증한 코드가 필요하다. [A·D 화면 관찰 합의](https://github.com/Bungeo-BeanOrCream/Hyojason/blob/D/docs/screen-observation-profile.md)

## N-07 Android 15 확인 항목

- 부모 통합 앱에서 `ProjectionCaptureService`에 바인딩하고, 활성 도움에서만 `approvalIntent`의 승인 결과를 `Controller.open`에 넘긴다. Settings에서 Clock으로 앱을 바꾸는 검증은 전체 화면 공유를 선택한다.
- 승인 시 프레임이 들어오고 통화가 종료돼도 동일한 도움에서 관찰이 지속되는지 확인한다. 시스템 공유 중지, 부모 종료, 권한 철회 각각에서 수신이 멈추고 로컬 관찰이 폐기돼야 한다.
- 캡처를 거절한 경우 프레임 소스가 없어야 한다. 이전 승인을 재사용하면 열리지 않아야 한다. 재승인에서 새 epoch만 발행되고 이전 epoch 프레임은 `STALE_BASIS`로 폐기되는지 확인한다.
- 로컬 검증용 APK는 레포 폴더 옆의 `N07-probe/app/build/outputs/apk/debug/app-debug.apk`에 있다. API 35 AVD 또는 기기에 설치하고 홈 화면의 ‘Hyojason 캡처 검증’을 연다. 승인 결과, epoch와 프레임 수만 보여 주며 프레임 내용은 표시하지 않는다.
- 2026-11-19에 `Hyojason_N07_API35`를 Google APIs x86_64 API 35 이미지로 생성하고, `emulator-5562`에 APK를 설치해 검증 앱을 열었다. 앱 시작까지 확인했다.
- ‘사용한 승인 다시 제출’의 기대 결과는 `DUPLICATE`이며 기존 세션의 epoch는 그대로다. 중지 후 재승인에서는 다른 epoch가 보여야 한다. ‘통화 종료 이벤트 모의’는 캡처를 멈추지 않고 기준 프레임 수를 기록한다. 실제 통화 종료는 부모 통합 APK에서 별도로 확인한다.

## B-06 재접속

- `71f54aa`에서 기존 표시를 지우고 예약 타이머를 취소하도록 했다. 현재 scope의 새 안정 화면을 서버에 보낸 뒤 `server_event_seq`보다 큰 안내만 N-19에서 재검증한다. 같은 도움의 재접속은 적용했던 지시 ID를 보존한다.
- 부모 앱의 직렬 이벤트 루프에서 `ParentResumeGate.applyReconnect`를 호출하고, 안정 화면 전송 직후 `onScreenObserved`를 호출한다. 이후 `GuideGate.evaluate`에 같은 재접속 상태를 전달한다. [D 참조 구현](https://github.com/Bungeo-BeanOrCream/Hyojason/blob/D/server/session/hyojason_session/client_resume_gate.py)

## 이슈 #1·#2 골격 PR 후보

- 사용자 승인 후 `origin/main`의 `8c76fed`에서 로컬 `A-gradle-skeleton`을 만들었다. `12f7e9e`는 `6205947`의 Gradle 골격이며, `e8f2d1a`는 `2664f12`의 `android/contract` 경로와 빈 `:rtc`·`:child`, 실행 가능한 `:app`을 담았다. 별도 체크아웃 `Hyojason-skeleton`의 `android/README.md`에 Android Studio 실행 방법이 있다.
- 골격의 API 35 APK와 네 Android 라이브러리를 빌드하고 공통 계약 JVM 테스트 5개를 확인했다. `origin/main...A-gradle-skeleton`의 변경은 `android/`에 있고, Kotlin 소스는 공통 계약과 debug 검증 화면에만 있다. APK의 앱 ID, SDK 버전, 런처도 확인했다.
- `:app`의 임시 화면은 `com.hyojason.app.debug`에 있고 debug 빌드에 포함된다. 오버레이 권한 요청과 하나의 테스트 테두리 표시·제거를 제공한다. 실제 캡처 검증은 N-07 서비스가 있는 `A`에서 연결한다. 골격의 화면은 해당 연결이 들어올 때까지 캡처를 대기 상태로 표시한다.
- 검증 화면은 화면 공유의 상태, epoch와 프레임 수만 표시한다. 실제 프레임은 수신할 때 즉시 닫는다.

## 골격 PR 사전 보안 점검

- 상위 모델과 교차 점검한 뒤 골격 브랜치 `e32106f`에서 Gradle을 8.14.7로 올렸다. 공식 배포판 SHA-256을 고정하고 Wrapper JAR도 공식 SHA-256과 대조했다. [Gradle 보안 권고](https://github.com/gradle/gradle/security/advisories/GHSA-pcp8-98xm-c6pr)
- 검증 화면이 여러 개 열려도 프로세스에서 테스트 테두리 하나만 유지한다. 어느 검증 화면에서든 제거할 수 있고, 권한 설정으로 이동할 때 먼저 제거한다. [Android 터치 전달 규칙](https://developer.android.com/reference/android/view/WindowManager.LayoutParams#FLAG_NOT_TOUCHABLE)
- 골격 브랜치의 API 35 debug·release APK와 JVM 계약 테스트 5개를 확인했다. release APK 매니페스트에 검증 Activity, 화면 캡처 서비스, 오버레이 권한이 없다. 실제 AVD의 하위 앱 터치 전달은 `UNVERIFIED`다.

## 이슈 #6 지원 대상 선택: 현재 구현 확인 (변경 없음, 2026-11-19)

코드 기준 확인만 했고 이름·동작은 바꾸지 않았다. 근거 파일은 `android/` 아래다.

1. **통화 종료와 재연결 구분, RTC 의존**
   - `observer`·`overlay` 메인 코드는 RTC·LiveKit 타입을 import하지 않는다. 관찰·게이트는 RTC 연결 상태를 직접 보지 않고 `help_id`, `mode_revision`, `projection_epoch`, `screen_revision` 같은 basis로만 움직인다.
   - `ParentResumeGate`(`overlay/.../gate/ParentResumeGate.kt`)는 "통화 종료"를 따로 구분하지 않는다. 서버가 준 `ParentReconnect`(재접속 응답)만 받아 표시·타이머·도움 캐시를 비우고, `latestBasis.help_id`가 같으면 같은 도움의 재접속으로 이어간다(지시 ID 보존). `help_id`가 없거나 달라지면 `GateClock.clear()`와 캐시 폐기를 한다.
   - 통화 종료 → `POST_CALL_AGENT` 전환은 `GateClock.applyMode`가 `mode_revision`으로 처리한다. 즉 모드는 RTC 상태가 아니라 서버가 내린 모드 이벤트에 의존한다.
   - 주의: `GuideGate.agrees()`(`GuideGate.kt:142`)가 후보의 `call_id`가 최신 basis의 `call_id`와 다르면 오래된 것으로 거부한다. 같은 도움에서 새 통화가 시작돼 `call_id`가 바뀌면 이전 후보는 `STALE_BASIS`가 된다. #6에서 연결 식별자를 Basis에 넣으면 이 비교와 같이 정리해야 한다.
2. **도움 종료 때만 캡처 중지·표시 폐기하는 진입점**
   - 캡처 중지: `ProjectionCaptureService.stop(helpId, epoch, diagnostic)` → `ProjectionSession.stop`. 시스템 공유 중지는 `onSystemStop` 콜백으로 같은 경로를 탄다. 통화 종료로는 호출되지 않는다.
   - Overlay·타이머·제안 폐기는 **하나로 묶인 진입점이 없다.** `GateClock.clear()`/`cancelTimer()`, `ParentResumeGate`의 `clearDisplay`·`cancelScheduledTimer`·`clearHelpCache` 콜백, `ParentGuideSuppression`이 각각 흩어져 있고, 부모 통합 앱(B가 채울 `:app`)이 직렬 이벤트 루프에서 순서대로 호출하는 구조다. "도움 종료" 이벤트 하나로 위 셋을 한꺼번에 부르는 함수는 아직 없다. 필요하면 별도 지시로 만든다(이번에는 만들지 않았다).
3. **새 도움 = 새 `projection_epoch` → 이전 응답 `STALE_BASIS`**
   - 새 승인마다 새 epoch를 발급한다(`ProjectionSession.open`의 `newEpoch`). 테스트 존재: `ProjectionSessionTest.newApprovalCreatesFreshEpochAndClosesThePreviousSource`(새 epoch·이전 소스 닫힘), `newestFrameWinsAndOtherEpochIsWiped`, `FrameDetectionTest`의 구 epoch 프레임 `STALE_BASIS`(171행), `ScreenStabilizerTest`의 구 epoch `STALE_BASIS`(81행)와 `B03_2_newEpochUsesPreviousScreenRevisionWithoutReset`.
   - (갱신) `ParentGuideFlowTest.newHelpOrNewEpochMakesOldGuideStaleAndClearsIt`가 구 epoch·다른 help_id의 안내가 `STALE_BASIS`로 폐기되고 표시가 지워지는 것을 확인한다. 아래 "없는 것"은 이 테스트 추가 전 기록이다.
   - 없는 것: **새 도움 시작 → 이전 도움의 기존 안내 응답이 `GuideGate`에서 `STALE_BASIS`로 폐기되는 통합 테스트**는 별도로 없다. `GuideGateTest:115`가 STALE_BASIS를 확인하지만 epoch 불일치 단독 케이스인지는 보강 확인이 필요하다. 실기기 재승인 흐름은 `UNVERIFIED`다.
4. **"부모 기기" 고정 표현 목록 (변경 안 함)**
   - 코드(`*.kt`)에는 한글 "부모 기기" 문자열이 없다. 이름에 박힌 것은 `ParentResumeGate`, `ParentReconnect`, `ParentGuideSuppression`(`overlay/.../gate/`), `ParentRunEvidence.kt`의 `ParentRun`·`ParentTiming`·`ParentObservation`(`app/.../debug/`)이고, 증거의 `clock_id`가 `parent_monotonic`이다.
   - 문서: `README.md:14`, `docs/architecture.md:15`·`:22`, `docs/implementation-contract.md:43`("부모 단말"), `docs/instruction-policy.md:22`("부모 단말"), `docs/small-nodes/N-02.md:60`, `docs/small-nodes/N-09.md:57`. 파일명·표현 변경은 #6 합의 후 별도 지시로 한다.

## 이슈 #5 trace_id 주입 지점 (2026-11-19)

- `ParentRun.traceId`는 null 허용이다. 서버가 발급한 값만 `HelpTraceIds.inject(helpId, traceId)`로 넣고, 앱이 직접 만들지 않는다.
- 주입되지 않은 기록은 `status=UNVERIFIED`이며 `observed_*Json`이 null을 돌려줘 판정용으로 내보내지 않는다. 호출 생성 응답 전 기록도 같다.
- 값은 `endHelp`에서만 지운다. 통화 종료·네트워크 끊김은 holder에 알리지 않으므로 유지되고, 다른 help_id로는 이전 값을 돌려주지 않는다.
- 봉투 필드명은 바꾸지 않았다. D의 `docs/contracts.md` PR 합의 뒤 필요하면 맞춘다. JVM 테스트 2개 추가(app 9개), 통과.

## N-20 부모 표시 구현 (2026-11-19)

- `overlay/surface`: 판단은 `ParentSurfaces.render`(JVM, 테스트 20.1~20.5 통과), Android 창은 `AndroidSurfaceHost`로 분리했다. 테두리 창은 `FLAG_NOT_TOUCHABLE`, 상단 띠는 끄기·통화로 버튼만 터치를 받는다.
- 결과: 표시 `success/shown`, 정리 `success/cleared`, 테두리만 막힘 `partial/banner_only`, 권한 없음·띠 창 실패 `error`(`OVERLAY_DENIED`/`WINDOW_FAILED`, 창 모두 제거).
- `UNVERIFIED`: 실제 터치 통과, 권한 철회 시 소멸, Settings 대상·Clock 저장 터치는 AVD·시연 기기에서 사람이 확인해야 한다. N-19 결과를 잇는 호출부는 `ParentGuideFlow`로 구현했다(아래 "B-06 호출부 연결"). 사용자 이벤트(끄기/통화로)를 N-22로 보내는 연결은 아직 없다.

## 진행 상태

- 현재 상태: 골격 PR #7(`A-gradle-skeleton`, e32106f 포함)과 #8·#9는 `main`에 머지됐다. 로컬 `A`는 머지 전 기준(merge-base `ce7dc38`)이라 `main`과 갈라져 있다. `A`에서 추가 지시 4~6(`ParentRunEvidence`, #6 확인, #5 trace_id 주입점)을 처리했다. 앱·라이브러리 빌드와 JVM 테스트 통과. 07.1~07.5의 Android 실동작은 `UNVERIFIED`다.
- 빌드 환경: AGP 8.10.0, Kotlin 2.2.10, Gradle 8.14.7, JVM 타깃 17, JUnit 4.13.2. Windows 한글 경로에서는 상위 폴더를 임시 드라이브 문자(`subst`)에 연결해 실행한다. SDK는 체크아웃 바깥의 `.android-sdk`를 사용한다.
- 갱신(2026-11-19): `A-rebased`(origin/main c3f3a84 위)에서 N-19→N-20 호출부 `ParentGuideFlow`를 구현했다(9e8549c 및 후속 테스트). 오버레이 JVM 테스트와 `:app:assembleDebug` 통과. 에뮬레이터 결과는 모두 `UNVERIFIED`다.
- 다음 할 일: 사람이 Mac에서 36.1 에뮬레이터로 확인한다. 항목과 검증 후 작업은 [A-emulator-verification.md](A-emulator-verification.md). 확인 후 사용자 확인을 받아 푸시·PR(Big Node별).
- 하면 안 되는 일: 계약 문서 변경, B 슬롯 기능 수정, `main` 직접 커밋, 승인 전 푸시·PR, fixture의 LIVE 처리, #6 합의 전 이름 변경, 에뮬레이터 항목의 에이전트 단독 PASS 선언, 36.1 결과를 Android 15로 기록.
- 막힌 것: 에뮬레이터 확인(사람), `A`/`A-rebased`와 `main` 정리 방식 승인, `trace_id` 발급 주체 답변(D), N-22 연결의 B/D 협의. claude-mem 로그인 만료(`/login` 필요).

## AGENTS.md 테스트 실행 명령 제안

Android SDK Platform 35, Build Tools 35.0.0과 JDK 17을 사용한다. 이 작업 환경에서는 JDK 23으로 확인했다. 체크아웃의 `android`를 열고 아래 명령을 실행한다. `:app:assembleDebug`는 실행 앱 모듈이 있는 브랜치에서 함께 실행한다.

```powershell
.\gradlew.bat :contract:test :observer:testDebugUnitTest :overlay:testDebugUnitTest :observer:assembleDebug :overlay:assembleDebug
.\gradlew.bat :app:assembleDebug
```

Windows 프로젝트 또는 SDK 경로에 한글이 있으면 사용 중이지 않은 드라이브 문자로 임시 연결한 뒤 빌드한다. 아래 명령은 이 작업 환경의 레포 루트에서 실행하는 예다.

```powershell
$checkout = (Get-Location).Path
$sourceDir = Split-Path $checkout -Parent
$checkoutName = Split-Path $checkout -Leaf
$drive = 'X:'
if (Get-PSDrive -Name ($drive.TrimEnd(':')) -ErrorAction SilentlyContinue) { throw "$drive is already in use" }
subst.exe $drive $sourceDir
if ($LASTEXITCODE -ne 0) { throw 'subst failed' }
$previousSdk = $env:ANDROID_HOME
try {
    $env:ANDROID_HOME = "$drive\.android-sdk"
    Push-Location "$drive\$checkoutName\android"
    try {
        .\gradlew.bat :contract:test :observer:testDebugUnitTest :overlay:testDebugUnitTest :observer:assembleDebug :overlay:assembleDebug :app:assembleDebug
        if ($LASTEXITCODE -ne 0) { throw 'Android build or test failed' }
    } finally {
        Pop-Location
    }
} finally {
    $env:ANDROID_HOME = $previousSdk
    subst.exe $drive /D
}
```

## B-06 호출부 연결 (2026-11-19)

- `ParentGuideFlow.run`(overlay/gate): N-19 `GuideGate.evaluate` → N-20 `ParentSurfaces.render`를 묶어 `NodeResult<ParentGuideData>`(node_id=B-06)를 만든다.
- 표시 없음/보호 화면/캡처 공백/동의 철회/종료/STALE_BASIS는 먼저 `host.clear()` 후 success/suppressed. `cancel_timer`면 `GateClock`을 취소한다.
- 정책 거부는 지정 문구만(success/policy_denied). 테두리 불가는 partial/banner_only, 권한·창 실패는 error(outcome=null).
- JVM 테스트 B06.1~B06.3과 구 epoch·help_id 폐기 포함 `ParentGuideFlowTest` 6건 통과. **B06.4(실제 Settings/Clock 터치 통과)는 UNVERIFIED — 사람이 AVD/실기기에서 확인해야 한다.**
- 남은 일: 사용자 이벤트(DISMISS_GUIDE/RETURN_TO_CALL)를 N-22로 보내는 연결(B/D 경로 협의), AVD 확인, PR은 사용자 확인 후.
