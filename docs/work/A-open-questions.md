# A 슬롯 구현 확인 사항

현재 담당: Codex (2026-11-19)

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

## 진행 상태

- 현재 상태: 골격 후보 `e8f2d1a`의 API 35 빌드와 계약 테스트 5개를 확인하고, 사용자 승인 후 `origin/A-gradle-skeleton`에 푸시했다. 원격 HEAD가 로컬과 일치한다. 기능 작업용 `A`는 실제 N-07 연결이 있는 debug 앱을 포함하며, 앱·라이브러리 빌드와 JVM 테스트 43개를 확인했다. 두 체크아웃의 APK 경로는 각각 `android/app/build/outputs/apk/debug/app-debug.apk`다. 07.1~07.5의 Android 실동작은 `UNVERIFIED`다.
- 빌드 환경: AGP 8.10.0, Kotlin 2.2.10, Gradle 8.11.1, JVM 타깃 17, JUnit 4.13.2. 현재 Windows 한글 경로에서는 상위 작업 폴더를 임시 드라이브 문자에 연결해 실행했다. SDK는 체크아웃 바깥 작업 폴더의 `.android-sdk`를 사용했다.
- 다음 할 일: 사용자 확인 후 `A-gradle-skeleton`에서 `main`으로 이슈 #1·#2 골격 PR을 연다. 기능 작업에서는 Android 15에서 N-07 승인·앱 전환·중지·재승인을 확인한 뒤 N-20을 진행한다.
- 하면 안 되는 일: 계약 문서 변경, B 슬롯 기능 수정, `main` 직접 커밋, 승인 전 브랜치 생성·rebase·푸시·PR, fixture의 LIVE 처리.
- 막힌 것: 골격 PR 생성 승인 대기. N-07의 Android 15 동작 확인 대기.

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
