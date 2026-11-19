현재 담당: Claude Code (2026-11-19) · D 슬롯

# 이슈 #3 D 검토본 · screen_kind·의미 전이·target_id

[이슈 #3](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/3)의 A 초안(2026-11-19 댓글)을 D가 검토한 표다. 이슈 제안대로 `docs/work/`에 정리하고, A가 확인하면 PR로 `docs/`에 반영한다. **합의 전 초안**이다.

근거: [scenarios.md](../scenarios.md) 지원 목표 표, [verification-plan.md](../verification-plan.md) 지시 표본 I01~I20, [contracts.md](../contracts.md) ScreenState·TrailEntry, [B-04](../big-nodes/B-04.md) B04.3.

검토 결과 요약

| 항목 | A 초안 | D 의견 |
|---|---|---|
| 값 형식 | 소문자 snake_case, `target_id`는 `<앱>.<대상>` | 동의 |
| screen_kind | 시연 경로 15개 + protected/unknown | 동의. 시연 경로에 필요한 화면 5개 추가, 메시지 접두사 `messages_` → `sms_` 제안 |
| 의미 전이 | kind 변경 또는 같은 kind의 target_ids 집합 변경 → screen_revision | **수정 제안**: 같은 kind에서는 상태 증거 변화만 screen_revision. 보이는 대상 변화(스크롤)는 layout_revision |
| target_id 중복 | `.<순번>`, 화면 위→아래 | **수정 제안**: 순번을 프로필 registry 고정 순서로, 형식은 `#<순번>` |
| 좌표 | LocalObservation에만 | 동의. 서버 HelpSnapshot에는 ScreenState만, N-13/N-21 응답에는 screen 자체가 없음(테스트로 확인) |

## 1. screen_kind 값

`D 추가`는 검증 표본·시나리오의 출발 화면이나 도착 근거 화면인데 초안에 없던 값이다.

| 값 | 화면 | 목표 | 비고 |
|---|---|---|---|
| `home` | 홈 화면(앱 아이콘) | 공통 | I06 출발 |
| `settings_home` | 설정 홈 | SET_FONT, SET_TIMEOUT | I01·I02 |
| `settings_display` | 설정 > 디스플레이 | SET_FONT, SET_TIMEOUT | I03·I04·I20. SET_TIMEOUT 완료 후 요약 확인 화면 |
| `settings_display_size_text` | 표시 크기 및 텍스트 | SET_FONT | 글자 크기 변화 확인 |
| `settings_screen_timeout` | 화면 자동 꺼짐 옵션 | SET_TIMEOUT | I05 |
| `clock_other_tab` | 시계 앱의 알람 외 탭(시계·타이머 등) | CLOCK_ALARM | **D 추가**. I07 “알람 탭 눌러 · 시계”의 출발 화면 |
| `clock_alarm_list` | 시계 > 알람 탭 | CLOCK_ALARM | I08. 저장 뒤 활성 알람 카드 확인 |
| `clock_alarm_edit` | 알람 편집 | CLOCK_ALARM | I09 |
| `maps_home` | 지도 홈 | MAP_ROUTE | I10 |
| `maps_search` | 검색어 입력·일치 항목 목록 | MAP_ROUTE | **D 추가**. “목적지 입력 → 일치 항목” 단계 |
| `maps_place_detail` | 장소 상세 | MAP_ROUTE | I11 |
| `maps_directions` | 길찾기 경로 화면 | MAP_ROUTE | **D 추가**. “목적지와 경로 화면 표시” = 도착 근거 |
| `sms_list` | 메시지 목록 | SMS_SEND | I12. 초안 `messages_list` |
| `sms_compose` | 새 대화(받는 사람 선택) | SMS_SEND | 초안 `messages_compose` |
| `sms_conversation` | 대화방(입력창·보내기) | SMS_SEND | **D 추가**. I13 “승인 시연 상대의 문자 초안”, 발신 상태 확인 |
| `gmail_nav_drawer` | Gmail 탐색 목록 | MAIL_OPEN | **D 추가**. I14 출발 화면 |
| `gmail_inbox` | 받은편지함 | MAIL_OPEN | I15 |
| `gmail_mail_detail` | 메일 상세 | MAIL_OPEN | 열림 증거만 |
| `protected` | 로그인·권한·보호 화면 | 공통 | 본문 미분석. target_ids·evidence_codes는 빈 배열 |
| `unknown` | 지원 목록 밖 | 공통 | I17·I18 같은 설정 하위 화면 포함 |

메시지 접두사: verification-plan의 target_id가 `sms.compose`, `sms.send`이고 목표 ID가 `SMS_SEND`라서 screen_kind도 `sms_*`로 맞추길 제안한다. A가 `messages_*`를 유지하길 원하면 그쪽으로 맞춘다.

## 2. 의미 전이 (screen_revision vs layout_revision)

| 변화 | A 초안 | D 제안 |
|---|---|---|
| screen_kind 변경 | screen_revision | 동의 |
| 같은 kind, 상태 증거 변화 (예: 10분 선택됨, 알람 카드 활성) | screen_revision | 동의 |
| 같은 kind, 스크롤로 **보이는** target_ids 집합 변화 | screen_revision | **layout_revision만** |
| 스크롤·키보드·위치 이동 | layout_revision | 동의 |
| 확정 시점 | 같은 의미 2회 연속 | 동의 |

수정 이유: N-27의 visible/below_fold 판단에 쓰려면 `target_ids`는 ‘지금 보이는 대상’이어야 한다. 그러면 초안 규칙에서는 스크롤만 해도 집합이 바뀌어 screen_revision이 오른다. 그 경우 N-12가 스크롤을 화면 전이 엣지로 기록하고, N-28이 지시 뒤 ‘화면 변화’로 오해할 수 있다. contracts.md도 “스크롤만으로 통화 중 타이머를 재시작하지 않는다”고 한다. 같은 kind 안에서는 아래 상태 증거 코드가 바뀔 때만 screen_revision을 올리자.

추가 규칙
- 캡처 공백(observation_health=GAP) 동안 revision을 올리지 않는다. 공백 뒤 첫 안정 화면은 새 screen_revision이지만 N-12는 공백 marker만 남기고 A→B를 엣지로 잇지 않는다(B04.3).
- projection_epoch가 바뀌어도(캡처 재승인) screen_revision을 0으로 되돌리지 않고 이어서 올린다. 서버는 (projection_epoch, screen_revision)을 함께 비교하지만, 단조 증가면 이력과 진단이 단순하다.

### 상태 증거 코드 (D 제안)

N-18 도착 추정과 N-25 판정에서 D가 쓰는 값이다. 본문·주소·OCR 문장 없이 코드만 보낸다.

| 목표 | evidence_code | 나오는 화면 |
|---|---|---|
| SET_FONT | `font_scale_changed` | `settings_display_size_text` |
| SET_TIMEOUT | `timeout_10m_selected` | `settings_screen_timeout`, `settings_display` |
| CLOCK_ALARM | `alarm_card_active`, `alarm_next_tomorrow` | `clock_alarm_list` |
| MAP_ROUTE | `route_shown` | `maps_directions` |
| SMS_SEND | `sms_sent_status` | `sms_conversation` |
| MAIL_OPEN | `mail_detail_open` | `gmail_mail_detail` |

## 3. target_id

규칙
- `<앱>.<대상>`에 동의. 선택지는 한정자를 붙인다: `<앱>.<대상>.<한정자>` (예: `settings.timeout.10m`).
- 앱 접두사는 verification-plan과 맞춘다: 홈 아이콘 `launcher.`, 메시지 `sms.`.
- **중복 라벨**: 초안의 “화면 위→아래 순번”은 스크롤하면 순번이 바뀌어 같은 버튼의 ID가 달라진다. 순번은 앱 프로필 registry에 적힌 고정 순서(전체 목록 기준)로 매기고, 한정자와 헷갈리지 않게 `#<순번>`으로 쓰자 (예: `clock.alarm_toggle#2`).

| target_id | 화면 | 출처 |
|---|---|---|
| `launcher.settings` · `launcher.clock` · `launcher.maps` · `launcher.sms` · `launcher.gmail` | `home` | I06(`launcher.clock`), 나머지 D 추가 |
| `settings.display` | `settings_home` | I01·I02 |
| `settings.display_size_text` | `settings_display` | I03·I20 |
| `settings.screen_timeout` | `settings_display` | I04 |
| `settings.timeout.10m` | `settings_screen_timeout` | I05 |
| `settings.font_size_larger` | `settings_display_size_text` | D 추가, **A 확인 필요**: scenarios의 “큰 쪽 조절 영역” |
| `clock.alarm_tab` | `clock_other_tab` | I07 |
| `clock.add_alarm` | `clock_alarm_list` | I08 |
| `clock.save_alarm` | `clock_alarm_edit` | I09 |
| `maps.search_box` | `maps_home` | I10 |
| `maps.demo_place_result` | `maps_search` | D 추가: “일치 항목” |
| `maps.directions` | `maps_place_detail` | I11 |
| `sms.compose` | `sms_list` | I12 |
| `sms.demo_recipient` | `sms_compose` | D 추가: 승인된 시연 상대 |
| `sms.text_field` | `sms_conversation` | D 추가: 내용 입력 위치(INPUT_HINT) |
| `sms.send` | `sms_conversation` | I13 |
| `gmail.inbox` | `gmail_nav_drawer` | I14 |
| `gmail.demo_mail` | `gmail_inbox` | I15 |

## 4. 열린 질문에 대한 D 답

1. **메시지 대상 ID**: 위 표의 `launcher.sms`, `sms.compose`, `sms.demo_recipient`, `sms.text_field`, `sms.send`. 보내기는 위치만 안내하고 실제 발송은 부모가 누른다. `sms.send`에 위험 문구(“되돌리기 어려운 동작이에요. 천천히 확인하세요”)를 붙일지는 A·C가 정한다.
2. **프로필 버전 표기 위치**: 공통 계약의 ScreenState에는 필드를 추가하지 않는다. 앱 프로필 registry 항목에 `profile_version`(앱 versionName + AVD 이미지)을 두고, 실행 중인 앱 버전과 맞지 않으면 추측하지 말고 `unknown`으로 분류한다. D는 N-24(`demo/fixtures`)에 시연 AVD의 앱·프로필 버전 매니페스트를 두고 N-25 결과의 “device/app/profile revision” 칸에 그대로 기록한다.

## 5. D 구현에 미치는 영향

- N-11·N-13·N-21은 screen_kind·target_id를 불투명 문자열로만 다룬다. 이 표의 값이 바뀌어도 코드는 바뀌지 않는다. 테스트 fixture는 이 초안 값(`settings_home`, `settings.display`)을 쓴다.
- N-12(전이 이력)는 2장의 screen_revision 규칙에 의존한다. 규칙이 합의되면 N-12 구현을 시작한다.
