# 실험 계획과 기대 결과

설정 라이브 A(자녀 주도 3초 개입)와 시계 라이브 B(통화 후 이어받기)를 별도 평가한다. 설정 2개, 시계, 지도, 문자, Gmail 기능 확인은 [지원 목표 표](scenarios.md)에서 진행한다.

## 표본·측정 방법

| ID | 입력·분모 | 기대 결과와 산식 | 증거 |
|---|---|---|---|
| V-01 라이브 완주 | 두 AVD에서 A/B 각각 5회. 첫 실패 후 이어서 성공해도 최초 run을 보존 | 각 시나리오 목표 4/5 완주. A는 3초 내 무표시, BACK, SCROLL_DOWN, TAP. B는 종료 후 실제 모델·부모 수행·도착·자녀 완료 모두 충족 | A/B 체크리스트, 동일 run trace, LIVE 모델 revision |
| V-02 지시 이해 | 자녀 확정 발화 20개. 버튼의 동의어, 모호함, 보호 조작, 지시 아닌 말, 최신 지시가 이전 것을 대체하는 경우 포함 | 17/20 이상. 기대 action 및 target_key 또는 기대 보류·차단이 맞으면 1건 성공. 같은 발화의 재시도를 별도 표본으로 세지 않음 | 발화 fixture ID, expected/actual, 결과 상태 |
| V-03 위치 찾기 | 고정 지원 앱·기기 프로필의 독립적인 위치 표본 20개 이상. 앱·화면, 보이는 버튼·아래 버튼·미확인 변형을 준비하고 실제 위치는 사람이 화면에서 검증 | 검증 가능한 대상 표본에서 correct/eligible ≥85%. visible은 target_id·정답 버튼 bounds 일치, below_fold는 정확한 컨테이너·방향. unknown·보호 표본은 오표시 0을 별도 집계. timeout·실등록 경로 누락도 적격 표본이면 오답 | profile ID, local ground truth, 모델 선택, capture 건강성. 원본 프레임과 좌표를 로그/서버로 보내지 않음 |
| V-04 개입 타이밍 | 실제 3초 이내 정답 조작과 독립 단조 시계 fixture로 2999/3000/3001ms를 재현. 버튼, 뒤로, 아래 개입 모두 측정 | 3초 이내 맞게 조작한 모든 건에서 테두리·조작 안내 0회. 무입력·오입력에서 3000ms 전 표시 0회, 준비되고 안전하면 3000ms부터 표시 가능. 지연된 판정은 보류와 지연을 별도 기록. 통화 후의 3초도 전환·안정 화면부터 별도 측정 | t0, first_observed, render_elapsed_ms, reason. 발화 말끝 → t0는 영상 공통 시간축으로 별도 측정 |
| V-05 목표 파악 | 목표·목표 없음·모호 요청·유효 슬롯·이미 있는 목표와 같거나 다른 명시적 목표를 포함한 독립 표본. 20개부터 시작 | 기대 goal_id와 필수 슬롯이 모두 일치하면 1건. 미지원/모호 요청에 목표를 지어내지 않음. 정확도와 실패 분류를 보고 | goal expectation, actual, 모델 질문 GOAL, 성능 및 support status |
| V-06 전환과 종료 | 유효/미확정 목표 상태에서 hangup, RTC 재연결, 못 찾은 도착, 중복/오래된 완료, 캡처 철회 | 확정 종료·유효 목표에서만 post-call. 통화 중/오류 시 도착 알림 0, 명시적 완료 1회, 철회 즉시 표시 0. 자녀 진행 화면에 지시/좌표/단계 없음 | call·mode·help 전이, shown/suppressed, 완료 알림 수 |
| V-07 정보와 독립성 | 연결, 재접속, 종료 뒤 새 지시, 로그·이벤트·앱 양쪽 보기 | 화면 영상 트랙 0, 영속 원본·개인 정보 0, 재시작 도움의 이전 지시/이력 reuse 0, 부모가 끈 경우 명시적 재동의 전 재시작 0 | 역할별 payload, 로그 샘플, cache 삭제 이벤트 |
| V-08 실제 모델과 비용 | L40S에서 GOAL, TARGET_LOCATION, ACTION_MATCH, NEXT_ACTION, ARRIVAL 각 1회 이상 실제 요청. 실패 후 출력 계약까지 검사 | 동일 model_revision과 실제 확률·지연, 결과 선택의 소속이 확인됨. 모델 모의 경로 통과를 라이브로 산입하지 않음. 총 NVIDIA 크레딧 $100 이하 | N-14 진단, N-26 차감·중지 기록 |

위치 20개는 실험의 최소 운영 표본이다. 평가는 고정한 프로필·임계값으로 실행하며 이후 개선 전·후를 같은 반복 실행으로 분리한다. 위치 정답 판독에는 합성 자산만 사용한다. 기기에서 위치를 검증하되 중앙 로그에는 일치 여부와 대상 ID만 남긴다.

## 통합 회귀 시험

| 케이스 | 주입·재현 조건 | 기대 상태 및 업무 결과 | 불변 조건 |
|---|---|---|---|
| E01 | 현재·이력의 필수 탐색 완료, 후보 0개 | N-16 success/no_candidates, 빈 배열 | N-27/N-17 생략. 목표 도착으로 처리 0회 |
| E02 | 현재 0개, 이력 공백 | N-16 partial/search_incomplete, coverage=partial, 빈 배열 | no_candidates로 변환 금지. 새 관찰 대기 |
| E03 | 현재 후보 직접 검증, 이력 오류 | N-16 partial/search_incomplete, 현재 후보만 남음 | 복귀 후보는 0. 직접 검증 후보에만 후속 호출 가능 |
| E04 | 오래된 epoch 또는 전체 검색 실패 | N-16 error/null, STALE_BASIS 또는 DEPENDENCY_UNAVAILABLE | 임의 BACK·스크롤·버튼 강조 0회 |
| E05 | 2900ms에 정답으로 보이는 화면 전이, 수행 모델 응답이 늦음 | 직전 로컬 변화로 N-19 waiting, 이후 followed | 테두리 발생 0회. 늦은 정답 때문에 이미 뜬 표시를 성공으로 집계하지 않음 |
| E06 | 2800ms에 다른 자녀 지시, 이전 응답이 3000ms에 도착 | N-11 새 instruction_revision, 이전 토큰 폐기 | 이전 t0로 안내 0회, 새 t0로 한 번만 계산 |
| E07 | 녹음 역할 착오 또는 부모의 “어디지?” | 부모 발화는 모델 트리거·타이머 갱신 없음. 소속 불일치 버림 | 부모 말로 3초 전에 표시 0회 |
| E08 | 다른 메뉴 진입 직후 캡처 공백 | ActionVerdict UNKNOWN 또는 WRONG + 검증 역경로 없음 | 잘못 눌렀다고 단정하지 않고 BACK 0회 |
| E09 | SCROLL_DOWN을 띄운 뒤 현재 지시 대상이 보이게 됨 | 같은 지시로 새 screen_revision과 위치 사용 | 예약 시각 변경 없음. 새 지시나 모델 NEXT_ACTION 호출 0회 |
| E10 | 통화 종료 직전 미해결 위치 결과를 지연 | N-11 POST_CALL_AGENT, 이전 mode_revision 결과 폐기 | 기존 3초·기한을 승계하지 않음 |
| E11 | 문자 삭제·공장초기화 또는 보호 화면 | policy_denied 또는 protected | 강조 0회. 안전한 화면에서 정확한 보호 문구만 가능 |
| E12 | 첫 지시 수신과 음성 목표가 같은 발화, 오디오 출력에 Bluetooth 존재 | N-03 1회 평가 및 기존 경로 유지 | 기기 출력 임의 전환 0회 |
| E13 | 지도 수신·메일 열기에서 기대 증거 부족 | N-18 success/not_arrived 또는 오류 | 자녀 도착 오탐 0회, 앱 밖 자동 입력·전송 0회 |
| E14 | Capture 재승인, 스크롤/키보드 또는 연결 순단 | 구 epoch 거부, local layout 재매칭, 순단은 POST_CALL 전환 없음 | 오래된 원·좌표를 다시 그리지 않음 |

## 실행 기록 계약

각 결과는 case_id, expected, actual, verdict(PASS/FAIL/UNVERIFIED), trace_id, source(LIVE/FIXTURE), device/app/profile revision, 관련 mode/instruction/goal/screen revision과 해당하는 model_revision을 갖는다. 필요한 증거를 못 얻은 표본은 UNVERIFIED이며, 라이브 완주 또는 검증된 실제 모델 성공 수에 넣지 않는다. 검증표는 설계이며 실제 측정값은 실행 후 기록한다.

## 지시 이해 표본 초안

모든 입력은 자녀 역할의 한국어 확정 발화다. 대상 앱·현재 화면을 고정하고 N-15의 action/target_key와 필요한 경우 N-27의 상태를 비교한다. 표현이 모호하여 보류가 정답인 예도 사전에 라벨링한다.

| 표본 | 입력 · 현재 화면 | 기대 해석 |
|---|---|---|
| I01 | “디스플레이 눌러” · 설정 홈 | TAP / settings.display |
| I02 | “화면 설정에 들어가 줘” · 설정 홈 | TAP / settings.display |
| I03 | “표시 크기 및 텍스트를 눌러 줘” · 디스플레이 | TAP / settings.display_size_text |
| I04 | “화면 자동 꺼짐을 눌러 줘” · 디스플레이 | TAP / settings.screen_timeout |
| I05 | “십 분을 골라” · 자동 꺼짐 옵션 | TAP / settings.timeout.10m |
| I06 | “시계 앱을 열어 줘” · 홈 | TAP / launcher.clock |
| I07 | “알람 탭 눌러” · 시계 | TAP / clock.alarm_tab |
| I08 | “플러스 눌러서 추가해” · 알람 탭 | TAP / clock.add_alarm |
| I09 | “저장 버튼을 눌러” · 알람 편집 | TAP / clock.save_alarm |
| I10 | “지도에서 검색창 터치해” · 지도 홈 | TAP / maps.search_box |
| I11 | “길찾기 버튼 눌러” · 검증된 장소 상세 | TAP / maps.directions |
| I12 | “새 문자 눌러 줘” · 메시지 목록 | TAP / sms.compose |
| I13 | “보내기 버튼 눌러” · 승인 시연 상대의 문자 초안 | TAP / sms.send |
| I14 | “받은편지함 눌러” · Gmail 탐색 목록 | TAP / gmail.inbox |
| I15 | “시연 메일 열어 줘” · 받은편지함, 한 건 일치 | TAP / gmail.demo_mail |
| I16 | “그 버튼 눌러 줘” · 후보 여러 개, 지칭 단서 없음 | TARGET_LOCATION의 target_unknown. 테두리 없음 |
| I17 | “공장초기화 눌러 줘” · 설정 | policy_denied. 위험 문구만 |
| I18 | “계정 삭제해 줘” · 계정 설정 | policy_denied. 위험 문구만 |
| I19 | “엄마 들려?” · 이전 지시 진행 중 | 지시 없음. 타이머와 현재 지시 유지 |
| I20 | “화면 자동 꺼짐 말고 글자 크기 눌러 줘” · 디스플레이, 이전 지시 있음 | TAP / settings.display_size_text. 이전 지시 취소, 새 타이머 |

ID는 앱 프로필의 초기 논리 ID다. 리허설 전에 실제 라벨과 화면 근거를 고정하고, 고정한 버전으로 20개를 평가한다. 부모 역할 입력 차단은 회귀 E07에서 별도 검사한다.
