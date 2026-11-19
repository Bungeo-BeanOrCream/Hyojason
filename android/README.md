# Hyojason Android

Android Studio에서 이 디렉터리를 열고 API 35 가상 기기 또는 연결된 기기로 `:app`의 debug 변형을 실행한다. 앱 ID는 `com.hyojason.app`, minSdk와 targetSdk는 35다.

## Mac에서 두 에뮬레이터로 직접 시험하기

1. Android Studio의 **Settings → Languages & Frameworks → Android SDK**에서 **Android 15 (API 35)** 플랫폼과 **Android SDK Build-Tools 35.0.0**을 확인한다. 프로젝트의 Gradle JDK는 17 이상을 사용한다.
2. **Tools → Device Manager**에서 ARM64 API 35 AVD를 두 대 실행한다. 설정 앱 안내를 검증할 기기는 시스템 언어를 **한국어**로 둔다. 상단 실행 구성에서 **app**을 선택하고, 기기 선택 메뉴에서 첫 AVD를 골라 **Run ▶**를 누른다. 두 번째 AVD에도 같은 구성을 실행한다.
3. Mac에서 [통합 백엔드](../server/README.md)를 실행한다. 브라우저나 터미널에서 `http://127.0.0.1:8000/healthz`를 확인한다. 에뮬레이터는 `10.0.2.2:8000`으로 Mac에 접속한다. UI/코드만 확인할 경우 음성 키 없이 실행할 수 있고 실제 통화에는 팀 LiveKit·RTZR 설정과 Zoro-1 연결이 필요하다.
4. AVD 한 대는 **부모님**, 다른 한 대는 **자녀**로 고른다. 한쪽에서 **코드 만들기**, 다른 쪽에서 **코드 입력**을 누른다. 화면의 실제 번호를 사용한다. 서버의 코드 수명은 10분이다. 양쪽에서 **동의해요**를 누르면 AI 안내가 가능해지고, 한쪽이 거절하면 음성통화만 사용할 수 있다.
5. **이번 도움 → 도움받는 사람 변경**에서 부모님을 고른다. 어느 기기에서든 전화를 걸고 다른 쪽에서 **받기**를 누른다. Android의 마이크 권한을 승인한다. 자녀 AVD의 **⋯ → Microphone → Enable Host Microphone Access**도 켜야 Mac 마이크로 말할 수 있다. Mac이 요청하면 Android Emulator에 마이크 접근을 허용한다. 같은 Mac에서 두 기기가 동시에 마이크를 쓰면 울림이 생길 수 있으므로 테스트에서는 말을 보낼 쪽만 켠다. [Android Emulator 마이크 설정](https://developer.android.com/studio/run/emulator-extended-controls)
6. 부모 AVD에서 **화면 안내 시작**을 누른다. 처음에는 시스템 화면에서 **다른 앱 위에 표시**를 허용한다. 효자손에 돌아와 **화면 안내 시작**을 다시 누르고 Android의 **전체 화면 공유**를 승인한다. 부모 AVD에서 기본 **설정** 앱을 열어 **디스플레이 및 터치** 메뉴가 보이는 상태로 둔다. 자녀가 “설정 화면에서 디스플레이를 눌러 주세요”라고 말한다.

| 확인 장면 | 기대 결과 |
| --- | --- |
| 올바른 버튼을 3초 안에 누름 | 오버레이가 뜨지 않는다. 3초는 기기에 지시가 적용된 시점부터 센다. |
| 버튼을 누르지 않고 기다림 | 위치 판단과 최신 화면 확인 뒤 지원 대상에만 빨간 테두리가 나타난다. 보호자 앱에는 표시 확인 상태가 나타난다. |
| 보호자의 목표와 안내를 확인 | “화면 자동 꺼짐 시간을 10분으로 바꾸자. 설정 화면에서 디스플레이를 눌러 줘.”처럼 지원 목표와 당장 할 행동을 함께 말한다. 서버가 목표를 승인하면 보호자 카드에 **현재 목적**이 나타난다. |
| 통화 종료 후 승계 | 승인된 목표가 있으면 같은 도움의 진행 상태를 유지한다. 서버가 도착을 추정하면 보호자 화면에서 확인 후 **도움 완료**를 누른다. 두 기기가 새 코드 화면으로 돌아오는지 본다. |
| 가족 연결 끊기 | 홈에서 **가족 연결 끊기**를 누르고 상대방도 연결 시작 화면으로 돌아오는지 확인한다. |

시계 알람은 목표 인식과 서버 통합의 예시다. 실제 Android 화면 프로필은 한국어 API 35의 기본 설정 앱 범위다. 화면을 너무 빨리 바꾸거나 캡처가 지연되면 테두리를 보류한다. 두 AVD의 실사용자 통화는 위 절차로 검증하고 서버 fixture 결과와 따로 기록한다.

| 모듈 | 역할 |
|---|---|
| `:app` | 부모·자녀 통합 앱 진입점 |
| `:contract` | Kotlin/JVM 공통 계약과 JUnit 테스트 |
| `:observer`, `:overlay` | A 슬롯 라이브러리 골격 |
| `:rtc`, `:child` | B 슬롯 라이브러리 골격 |

## 임시 AVD 검증 화면

앱 아이콘은 통합 통화 화면을 연다. 오버레이나 화면 공유만 분리해서 확인하려면 debug APK를 설치한 뒤 검증 화면을 직접 연다.

```sh
adb shell am start -n com.hyojason.app/.debug.VerificationActivity
```

오버레이 권한을 설정한 뒤 테스트 테두리를 표시하고 제거할 수 있다. 실제 캡처 시작·중지와 프레임 수 확인도 지원한다. [통합 백엔드 실행](../server/README.md)

## 빌드와 테스트

Android SDK Platform 35, Build Tools 35.0.0 및 JDK 17로 실행한다.

Windows에서는 Android Studio 동기화와 CLI 빌드에 영문 경로를 사용한다. 한글 경로에 작업 폴더가 있으면 `subst`로 영문 드라이브 경로를 만들어 그 경로에서 프로젝트를 연다.

```powershell
.\gradlew.bat :contract:test :observer:testDebugUnitTest :overlay:testDebugUnitTest :rtc:testDebugUnitTest :child:testDebugUnitTest :app:assembleDebug
```
