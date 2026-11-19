# 기술 근거와 선행 검증

## 제품과 참고 문서

- 제품의 문제·사용자·지원 목표는 사용자가 전달한 PRD v2를 따른다. 실시간 개입, 인계와 시연 A·B는 이후 전달받은 변경사항을 반영했다.
- 문서 계층은 ReportAnchor JS의 Big/Small Node 표와 공통 결과 계약을 참고했다. 효자손의 실시간 실행 구조는 해당 저장소의 문서 질의 처리 구조와 별개다. [ReportAnchor JS](https://github.com/Jungminii-1114/ReportAnchor/tree/ad430ab123c61332e099765ab3e1a21c81c8dc7e/docs)

## 사용하는 공식 경로

| 기능 | 공식 문서 | 첫 통과 조건 |
|---|---|---|
| 두 Android 참가자 통화 | [LiveKit Android](https://docs.livekit.io/transport/sdk-platforms/android/) · [오디오 발행](https://docs.livekit.io/transport/media/publish/) | 마이크를 각각 개별 식별. 두 역할 앱 통화 유지 후 앱 밖으로 전환하고, 종료 후 도움 채널 유지 |
| 사용자 승인 화면 관찰 | [MediaProjection](https://developer.android.com/media/grow/media-projection) · [FGS 유형](https://developer.android.com/develop/background-work/services/fgs/service-types) | 동의·서비스 순서, 목표가 있는 상태에서 통화만 종료할 때 캡처 유지, 시스템 중단 콜백 확인 |
| 전면 표시 | [Overlay 권한](https://developer.android.com/reference/android/Manifest.permission#SYSTEM_ALERT_WINDOW) · [터치 검사](https://developer.android.com/about/versions/12/behavior-changes-all#untrusted-touch-events) · [보호 화면](https://developer.android.com/security/fraud-prevention/activities) | 실제 Settings와 Clock에서 빨간 표시 아래의 터치가 가능. 민감 화면에서는 제거. 표시는 환경마다 제한될 수 있음 |
| 가상 기기 | [AVD](https://developer.android.com/studio/run/managing-avds) · [마이크](https://developer.android.com/studio/run/emulator-extended-controls) · [가상 기기 주소](https://developer.android.com/studio/run/emulator-networking-address) | 두 AVD의 동일 SDK/언어, Google Maps/Gmail, 각각의 오디오 라우팅. 에뮬레이터의 localhost와 Mac의 주소를 구분 |
| 시연 문자 | [두 에뮬레이터 사이 SMS](https://developer.android.com/studio/run/emulator-networking-voice) | 확인된 상대 AVD 콘솔 포트로 시연 문자 보내기. 수신도 검증하고, 실제 통신사 전송으로 분류하지 않음 |
| 한국어 라벨 후보 | [ML Kit Android 텍스트 인식](https://developers.google.com/ml-kit/vision/text-recognition/v2/android) | 한국어 지원 경로에 대해 테스트. 렌더된 버튼이나 아이콘은 OCR과 별도로 탐지 |
| System 1 | [Zoro-1 모델 카드](https://huggingface.co/trillionlabs/Zoro-1) · [Trida Decision](https://trida.trillionlabs.co/decision/) | 모델 접근과 revision, typed decision entrypoint, 옵션과 실응답 확률 호환 검증. 발표용 설정은 실제 호출로 검증 |
| 비용 | [L40S 사양](https://www.nvidia.com/en-us/data-center/l40s/) | 콘솔의 크레딧 적용 범위·단가·해제 시 정산을 기록. $100 한도 사용 |

## 공개 문서와 시연 자산

표의 기술 경로는 공식 지원 범위다. 이 프로젝트에서 조합이 작동했는지는 실측 보고서에 별도로 기록한다. 공개 저장소에는 실사용자 음성·사진, 스크린 캡처 원본, Gmail 계정과 전화번호, .env·토큰을 올리지 않는다. 테스트 자료는 가상의 내용으로 준비하고 데모 영상은 두 역할의 동의를 받아 별도 관리한다.
