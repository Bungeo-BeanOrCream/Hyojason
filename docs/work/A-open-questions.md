# A 슬롯 구현 확인 사항

현재 담당: Codex (2026-11-19)

## N-19에서 최신성 검증 실패 시 반환할 결과

- A 슬롯 브리프 4절과 [`instruction-policy.md`](../instruction-policy.md)는 이전 모드·지시·화면 등으로 현재 표시 조건을 충족하지 못하면 `suppressed`를 지정한다.
- [`N-19.md`](../small-nodes/N-19.md)의 반환 표와 19.8은 무효 스코프·권한·로컬 유효성 실패를 `error`로 지정한다. 이 경우 `outcome`과 `data`는 null이다. [`B-06.md`](../big-nodes/B-06.md)는 상위 결과에 `success/suppressed`를 정의한다.
- 2026-11-19 사용자 결정. 이전 제안의 `Basis`가 최신 상태와 다르면 N-19는 `error`와 `STALE_BASIS`를 반환한다. B-06은 기존 표시를 즉시 지우고 `success/suppressed`로 처리한다. 19.8과 B06.3에서 각각 검증한다.

## N-09 한국어 OCR 엔진

- 4단계 시작 전 실제 OCR 엔진을 확정한다. 제안은 APK에 모델을 포함하는 [Google ML Kit 한국어 Text Recognition](https://developers.google.com/ml-kit/vision/text-recognition/v2/android?hl=ko)이다. 시연 기기에서 별도 모델 다운로드 없이 시작할 수 있다.
- 이슈 #3의 화면과 대상 ID 초안은 검출 프로필에 적용할 때 확인한다. 스크롤로 달라질 수 있는 가시 대상 목록과 의미 화면을 판별하는 프로필의 컨트롤 집합은 구분한다.

## 진행 상태

- 현재 상태: 로컬 `A`에서 0~3단계를 커밋했다. `6205947`은 observer·overlay API 35 골격, `7c49bde`는 공통 계약, `31a11ae`는 N-19/B-06의 게이트와 억제, `7cb473f`는 N-10 안정화다. 두 라이브러리 assembleDebug와 JVM 테스트 19개가 통과했다.
- 빌드 환경: AGP 8.10.0, Kotlin 2.2.10, Gradle 8.11.1, JVM 타깃 17, JUnit 4.13.2. 현재 Windows 한글 경로에서는 상위 작업 폴더를 임시 드라이브 문자에 연결해 실행했다. SDK는 체크아웃 바깥 작업 폴더의 `.android-sdk`를 사용했다.
- 다음 할 일: OCR 선택 후 4단계 N-08/N-09 fixture 및 프로필 검출. 이후 N-07 캡처, N-20 Android 창 렌더, Settings·Clock의 AVD/실기기 검증.
- 하면 안 되는 일: 계약 문서와 다른 슬롯 경로 수정, `main` 직접 커밋, 확인 전 푸시·PR, fixture의 LIVE 처리.
- 막힌 것: 실제 한국어 OCR 엔진 선택 대기. MediaProjection과 Overlay 실화면은 `UNVERIFIED`.
