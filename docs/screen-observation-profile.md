# 화면 관찰 프로필과 대상 ID

부모의 N-09·N-10이 내는 화면 분류를 제어 서버의 N-11·N-12와 Agent가 공유한다. 아래 값과 개정 규칙은 A·D가 합의했다. 앱별 인식 단서와 실제 시연 환경은 AVD에서 확인한다. [이슈 #3의 합의](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/3#issuecomment-7006659871)

## 화면 목록

| `screen_kind` | 화면 | 시연 목적 |
|---|---|---|
| `home` | 홈 화면의 앱 아이콘 | 시연 앱 시작 |
| `settings_home` | 설정 홈 | 글자 크기·화면 꺼짐 설정 시작 |
| `settings_display` | 디스플레이 메뉴 | 표시 크기·자동 꺼짐 진입과 꺼짐 설정 요약 확인 |
| `settings_display_size_text` | 표시 크기 및 텍스트 | 글자 크기 변화 확인 |
| `settings_screen_timeout` | 화면 자동 꺼짐 옵션 | 10분 선택 |
| `clock_other_tab` | 시계의 알람 외 탭 | 알람 탭으로 이동 |
| `clock_alarm_list` | 알람 목록 | 알람 추가와 활성 알람 카드 확인 |
| `clock_alarm_edit` | 알람 편집 | 시각 설정과 저장 |
| `maps_home` | 지도 홈 | 목적지 검색 시작 |
| `maps_search` | 검색 입력·결과 | 일치하는 시연 장소 확인 |
| `maps_place_detail` | 장소 상세 | 길찾기 버튼 선택 |
| `maps_directions` | 길찾기 경로 | 목적지와 경로 확인 |
| `sms_list` | 메시지 목록 | 새 대화 시작 |
| `sms_compose` | 새 메시지의 수신자 선택 | 시연 수신자 선택 |
| `sms_conversation` | 대화방 | 초안 입력·전송 상태 확인 |
| `gmail_nav_drawer` | Gmail 탐색 메뉴 | 받은편지함으로 이동 |
| `gmail_inbox` | 받은편지함 | 시연 메일 선택 |
| `gmail_mail_detail` | 메일 상세 | 열림 근거 확인 |
| `protected` | 로그인·권한·보호 화면 | `PROTECTED` 상태만 전송 |
| `unknown` | 미지원·미분류 화면 | `GAP` 상태만 전송 |

## 화면과 레이아웃 개정

- `screen_revision`은 안정된 `screen_kind`가 바뀌거나 같은 화면의 승인된 상태 증거가 바뀔 때 증가한다. 같은 의미를 두 번 연속 관찰해 안정 여부를 확인한다.
- 스크롤, 키보드, 배치 이동, 보이는 대상의 변화는 부모의 `layout_revision`으로 추적한다. `target_ids`는 현재 보이는 승인 대상이다. 표시 직전에 부모가 최신 로컬 위치를 다시 확인한다.
- 관찰 공백 동안에는 화면 버전이 그대로다. 공백 뒤 첫 안정 화면의 버전을 올리고 새 관찰 구간을 시작한다. 캡처를 재승인해 `projection_epoch`가 바뀌어도 화면 버전은 이어서 증가한다.
- N-10은 연속 관찰에서 확인한 전이에 이전 `from_screen_revision`과 승인된 근거를 보낸다. N-12는 공백을 가로지르는 엣지를 만들지 않는다. 같은 화면 안의 상태 변화는 수행 근거로 사용하며 복귀 엣지를 만들지 않는다.

| 목표 | 승인 상태 근거 | 화면 |
|---|---|---|
| `SET_FONT` | `font_scale_changed` | `settings_display_size_text` |
| `SET_TIMEOUT` | `timeout_10m_selected` | `settings_screen_timeout`, `settings_display` |
| `CLOCK_ALARM` | `alarm_card_active`, `alarm_next_tomorrow` | `clock_alarm_list` |
| `MAP_ROUTE` | `route_shown` | `maps_directions` |
| `SMS_SEND` | `sms_sent_status` | `sms_conversation` |
| `MAIL_OPEN` | `mail_detail_open` | `gmail_mail_detail` |

## 대상 ID와 프로필 버전

대상 ID는 `<앱>.<대상>`과 필요한 한정자로 구성한다. 같은 라벨의 대상은 프로필 레지스트리가 정한 고정 순번을 `#<n>`으로 붙인다. 예를 들어 `clock.alarm_toggle#2`는 화면에서 보이는 순서가 바뀌어도 동일한 대상이다.

| 화면 | 대표 `target_id` |
|---|---|
| `home` | `launcher.settings`, `launcher.clock`, `launcher.maps`, `launcher.sms`, `launcher.gmail` |
| `settings_home` | `settings.display` |
| `settings_display` | `settings.display_size_text`, `settings.screen_timeout` |
| `settings_display_size_text` | `settings.font_size_larger`는 AVD 확인 전 잠정 이름 |
| `settings_screen_timeout` | `settings.timeout.10m` |
| `clock_other_tab` | `clock.alarm_tab` |
| `clock_alarm_list` | `clock.add_alarm` |
| `clock_alarm_edit` | `clock.save_alarm` |
| `maps_home` | `maps.search_box` |
| `maps_search` | `maps.demo_place_result` |
| `maps_place_detail` | `maps.directions` |
| `sms_list` | `sms.compose` |
| `sms_compose` | `sms.demo_recipient` |
| `sms_conversation` | `sms.text_field`, `sms.send` |
| `gmail_nav_drawer` | `gmail.inbox` |
| `gmail_inbox` | `gmail.demo_mail` |

`sms.send`는 시연 수신자와 초안이 확인된 경우에 위치를 안내하며 실제 발송은 부모가 수행한다. 위험 문구 적용 여부는 이슈 #3에서 의견을 확인한다.

앱 프로필 레지스트리의 `profile_version`에 앱 `versionName`과 AVD 이미지를 기록한다. 설치된 앱·이미지와 맞지 않으면 `unknown`으로 분류한다. Rect, 본문, 주소, 전화번호는 부모 기기에 둔다. 서버는 분류와 승인 ID·근거 코드·개정 정보만 받는다.

[공통 자료형](contracts.md) · [지원 목표](scenarios.md) · [지시 표본 I01~I20](verification-plan.md)
