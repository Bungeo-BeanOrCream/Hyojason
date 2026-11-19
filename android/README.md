# Hyojason Android

Android Studio에서 이 디렉터리를 열고 API 35 가상 기기 또는 연결된 기기로 `:app`의 debug 변형을 실행한다. 앱 ID는 `com.hyojason.app`, minSdk와 targetSdk는 35다.

| 모듈 | 역할 |
|---|---|
| `:app` | 부모·자녀 통합 앱 진입점 |
| `:contract` | Kotlin/JVM 공통 계약과 JUnit 테스트 |
| `:observer`, `:overlay` | A 슬롯 라이브러리 골격 |
| `:rtc`, `:child` | B 슬롯 라이브러리 골격 |

## 임시 AVD 검증 화면

debug 빌드는 `com.hyojason.app.debug.VerificationActivity`를 실행한다. 오버레이 권한을 설정한 뒤 테스트 테두리를 표시하고 제거할 수 있다. 캡처 버튼은 N-07 연결이 들어올 때까지 대기 상태다. 기능 브랜치 `A`의 검증 화면은 실제 캡처 시작·중지와 프레임 수 확인을 지원한다.

## 빌드와 테스트

Android SDK Platform 35, Build Tools 35.0.0 및 JDK 17로 실행한다.

```powershell
.\gradlew.bat :contract:test :observer:testDebugUnitTest :overlay:testDebugUnitTest :rtc:testDebugUnitTest :child:testDebugUnitTest :app:assembleDebug
```
