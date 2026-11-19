# N-24·N-25 리허설 준비와 검증

두 AVD의 준비 상태는 N-24가 시나리오별로 확인한다. N-25는 고정 기대값과 실제 관측을 비교해 결과와 출처를 남긴다. [리허설 A·B 재현 단계](../../docs/scenarios.md) · [V-01~V-08와 E01~E14](../../docs/verification-plan.md)

## 모의 입력으로 실행

저장소 루트에서 다음 명령을 실행한다. Python 3.11 이상이며 앱·모델 호출과 외부 패키지 설치 없이 실행할 수 있다.

```sh
cd server/session
python -m hyojason_session.demo_verify prepare ../../demo/fixtures/manifest.fixture.json
python -m hyojason_session.demo_verify plan ../../demo/fixtures
python -m hyojason_session.demo_verify evaluate ../../demo/fixtures ../../demo/fixtures/evidence.fixture.json
```

- N-24는 예시에서 A·B와 앱 5개의 목표 6개가 준비됐다고 반환한다. 이 결과의 출처는 `FIXTURE`다.
- N-25 예시는 I01의 모의 성공과 I02의 최초 실패를 확인한다. I02의 재시도 성공은 표본을 대체하지 않는다. 나머지는 `UNVERIFIED`이며 `live_pass_count`는 0이다.
- `plan`은 A·B 각 5회, I01~I20, 위치 22개, 목표 20개, 타이밍·회귀·종료·개인정보 케이스와 모델·비용 점검의 입력 ID를 나열한다. 발화·위치·목표의 기대값은 [지시 표본](instructions.json), [위치 표본](positions.json), [목표 표본](goals.json)에 있다.

## 두 AVD에서 준비 상태 확인

`manifest.fixture.json`의 필드 구조를 사용해 실제 확인 결과를 별도의 로컬 매니페스트에 작성한다. 앱 또는 AVD 이미지·권한·시나리오 자산이 바뀌면 `fixture_revision`을 갱신하고 다시 검사한다.

| 준비 항목 | 확인 절차 |
|---|---|
| Android와 역할 | 같은 Android 이미지와 한국어 UI를 사용한다. 같은 효자손 APK를 부모·자녀 역할로 각각 열고 동의 및 계정 상태를 확인한다. |
| 제어 연결과 오디오 | 호스트에서 서버에 도달하는지와 AVD 앱에서 제어 요청이 성공하는지 각각 확인한다. AVD의 루프백은 AVD 자신을 가리킨다. 두 역할이 번갈아 말해 상대방이 들을 수 있는지 확인하고 마이크와 실제 스피커·유선·Bluetooth 경로를 기록한다. |
| 캡처와 관찰 | 부모의 화면 공유와 안내 권한, 앱별 시작 화면, 권한·로그인·보호 화면을 확인한다. 앱 버전과 합의한 `profile_version`을 기록한다. |
| 설정과 시계 | 초기 글자 크기와 화면 꺼짐 설정을 기기에 기록해 리허설 뒤 복구한다. A의 빠른 정답·오선택·역전이·아래쪽·현재 보이는 대상을 준비한다. B는 부모 기기의 시간대·날짜를 확인하고 다음 날 오전 6시 알람의 실제 예정 시각을 검증한다. |
| 지도·문자·메일 | 지도는 공개 시연 장소와 정상 네트워크를 확인한다. 문자는 승인된 시연 상대와 문구를 준비하고 상대 AVD에서 수신을 확인한다. Gmail은 이미 로그인된 시연 계정에서 준비된 메일을 연다. |

실제로 확인한 프로필과 자산만 `LIVE`로 기록하고 `prepare <로컬-매니페스트> --source LIVE`로 점검한다. `partial/some_fixtures_blocked`이면 준비된 시나리오만 진행한다. 필수 역할을 준비할 수 없거나 준비된 시나리오가 없으면 진단의 차단 단계를 확인한다. 계정·번호·토큰·실제 서버 주소와 좌표는 매니페스트에 기록하지 않는다. 자산에는 애플리케이션에서 확인 가능한 논리 ID를 쓴다.

## 관측 결과 수집

`evaluate ../../demo/fixtures <로컬-증거-JSON> --source LIVE`로 실측 파일을 평가한다. 파일의 최상위 키는 `observed_runs`이며 각 행은 다음 정보를 가진다. 전체 필드와 체크리스트는 [N-25 자료형](../../server/session/hyojason_session/demo_evaluation.py)에서 확인할 수 있다.

| 공통 키 | 값 |
|---|---|
| `case_id`, `attempt`, `trace_id` | 고정 표본 ID, 최초 1부터 시작하는 실행 번호, 관련 이벤트와 노드에서 공유하는 추적 ID |
| `source`, `input_source`, `screen_source`, `model_source` | 전체 기록과 발화·화면·모델 증거 각각의 `LIVE` 또는 `FIXTURE` |
| `device_revision`, `app_revision`, `profile_revision`, `model_revision` | N-24에서 확인한 기기·앱·프로필과 실제 모델의 버전. 모델을 호출한 경우 해당 모델 revision 포함 |
| `basis`, `actual`, `failure_domain` | 공통 Basis, 케이스별 측정값, 실패 시 `FUNCTION`·`DEVICE`·`CONTRACT` 중 확인된 구간 |

V-04의 시각은 부모의 같은 단조 시계에서 기록한다. 통화 중에는 첫 지시 수신, 통화 후에는 새 안정 화면이 기준이다. 발화 종료부터 부모 수신까지는 공통 시간축에서 확인한 별도 지연을 입력한다. 위치의 실제 bounds는 부모 기기에서 확인하고 보고서에는 일치 여부만 남긴다. V-05의 공개 장소·테스트 수신자·테스트 메일은 표본의 승인된 논리 ID로 기록한다.

먼저 A·B 각 1회의 추적을 연결하고 각 5회로 늘린다. 로그의 실패·보류와 최초 시도를 그대로 남긴다. 모델은 N-14·N-26이 검증한 다섯 질문의 실제 L40S 결과·모델 revision·확률·지연과 크레딧 장부를 사용한다. V-08은 V-01~V-07과 따로 보고한다. 평가 결과를 공유하기 전에는 원본 화면·오디오·본문·좌표가 포함되지 않았는지 확인한다.
