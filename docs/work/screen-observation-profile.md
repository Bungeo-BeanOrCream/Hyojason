# A·D 화면 관찰 연동 초안

A가 제안한 분류와 개정 기준에 D가 필요한 메시지 대상 ID와 프로필 버전 위치를 보완했다. 화면 분류와 국내 AVD의 실제 단서는 A·D 검토에서 확인한다. [이슈 #3](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/3) · [A의 초안](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/3#issuecomment-7005045229)

## 최소 화면 목록

| `screen_kind` | 화면 | 목표 | 대표 `target_id` |
|---|---|---|---|
| `home` | Android 홈의 앱 아이콘 | 공통 | `launcher.clock` |
| `settings_home` | 설정 홈 | `SET_FONT`, `SET_TIMEOUT` | `settings.display` |
| `settings_display` | 디스플레이 | `SET_FONT`, `SET_TIMEOUT` | `settings.display_size_text`, `settings.screen_timeout` |
| `settings_display_size_text` | 표시 크기 및 텍스트 | `SET_FONT` | 글자 크기 조절 대상은 실제 AVD 확인 후 등록 |
| `settings_screen_timeout` | 화면 자동 꺼짐 옵션 | `SET_TIMEOUT` | `settings.timeout.10m` |
| `clock_alarm_list` | 시계의 알람 목록 | `CLOCK_ALARM` | `clock.alarm_tab`, `clock.add_alarm` |
| `clock_alarm_edit` | 알람 편집 | `CLOCK_ALARM` | `clock.save_alarm` |
| `maps_home` | 지도 홈 | `MAP_ROUTE` | `maps.search_box` |
| `maps_place_detail` | 장소 상세 | `MAP_ROUTE` | `maps.directions` |
| `messages_list` | 메시지 목록 | `SMS_SEND` | `sms.compose` |
| `messages_compose` | 새 대화 또는 시연 문자 작성 | `SMS_SEND` | `sms.send` |
| `gmail_inbox` | 받은편지함 | `MAIL_OPEN` | `gmail.inbox`, `gmail.demo_mail` |
| `gmail_mail_detail` | 시연 메일 상세 | `MAIL_OPEN` | 완료 근거만 확인 |
| `protected` | 로그인·권한·보호 화면 | 공통 | 부모가 `PROTECTED` 상태만 전송 |
| `unknown` | 미지원 또는 분류가 불확실한 화면 | 공통 | 부모가 `GAP` 상태만 전송 |

메시지는 [실험 계획](../verification-plan.md)의 I12·I13과 동일하게 `sms.compose`와 `sms.send`부터 지원한다. `sms.send`는 시연 수신자와 초안이 확인된 경우에만 부모 앱이 후보로 제공한다. 수신자, 전화번호, 본문은 부모 기기에 둔다. 목표별 시연 경로는 [지원 목표·시연](../scenarios.md)을 따른다.

## 의미 전이와 레이아웃

| 관찰된 변화 | 개정 방식 | 서버 처리 |
|---|---|---|
| 등록된 다른 화면으로 이동 | `screen_revision` 증가 | 연속 관찰한 전후 화면과 근거로 전이 기록 |
| 같은 화면에서 옵션 선택 등 의미 있는 상태 또는 조작 가능한 컨트롤 집합이 변경 | `screen_revision` 증가 | 수행 근거로 사용할 수 있다. 같은 화면 안의 변경에는 복귀 엣지를 만들지 않는다. |
| 스크롤로 버튼이 보이거나 가려짐, 키보드 표시, 위치만 이동 | 부모의 `layout_revision`만 증가 | 같은 화면의 대상 가시성을 갱신하고 표시 전에 로컬 위치를 다시 확인 |
| 빈 캡처, 보호 화면, 미지원·불확실 화면 | 관찰 상태를 `GAP` 또는 `PROTECTED`로 갱신 | 현재 화면 근거를 무효화하고 재관찰 뒤 새 구간 시작 |

부모 관찰기는 같은 의미가 두 번 연속 확인된 안정 관찰을 기준으로 버전을 확정한다. 화면 의미와 실제로 보이는 대상의 목록을 따로 판단한다. 안정 전후 화면이 동일 캡처에서 이어졌다면 `screen.observed`에 `from_screen_revision`과 승인된 근거를 담는다. 검증된 복귀는 동일 앱의 등록된 방향과 관찰 근거를 함께 충족해야 한다.

## 대상 ID와 프로필 버전

- 화면 분류는 소문자 `snake_case`를 쓴다. 대상 ID는 `<앱>.<대상>`으로 시작하고 `settings.timeout.10m`처럼 필요한 경우 의미 있는 접미사를 붙인다.
- 동일 라벨의 대상은 프로필에 위→아래, 왼→오른쪽 순으로 최초 등록한 `.<순번>`을 고정한다. 런타임 스크롤이나 배치 변화에 따라 번호를 다시 매기지 않는다. 앱·화면·부모 로컬의 안정된 단서로 구분이 확인될 때만 등록 ID를 사용한다.
- AVD나 앱 버전별 라벨·리소스 차이는 부모의 앱 프로필 레지스트리에서 `profile_revision`, Android 이미지 및 앱 버전과 함께 관리한다. 같은 의미의 대상 ID를 유지하고 시연 로그에 사용한 프로필 버전을 남긴다.
- Rect는 부모의 `LocalObservation`에만 둔다. 제어 서버에는 화면 분류, 대상 ID, 승인된 근거 코드와 버전만 전달한다.

[공통 자료형](../contracts.md) · [서버 연결 방법](../../server/session/README.md)
