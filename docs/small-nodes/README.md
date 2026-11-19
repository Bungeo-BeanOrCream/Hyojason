# Small Node 목록

각 문서는 단일 책임, 입력·출력, 구체 처리, 오류와 구현 완료 기준을 관리한다. 실행 순서와 실패 시 후속 분기는 [Big Node](../big-nodes/README.md)에 둔다. 메시지와 결과의 공통 키는 [공통 계약](../contracts.md)을 따른다.

| Small Node | Big Node | 담당/실행 위치 | 책임 |
|---|---|---|---|
| [N-01 · resolve_pair](N-01.md) | [B-01](../big-nodes/B-01.md) | B · 세션 서버/Android 온보딩 | 승인된 부모와 자녀 한 명으로 페어를 제한하고 화면에 표시할 관계만 반환한다. |
| [N-02 · resolve_consent](N-02.md) | [B-01](../big-nodes/B-01.md) | B · Android/서버 | 역할별 동의와 시스템 권한을 확인하여 음성 인식, 캡처와 표시가 가능한지 구분한다. |
| [N-03 · start_or_close_call](N-03.md) | [B-01](../big-nodes/B-01.md) | B · Android/RTC/서버 | 부모 발신·자녀 수신과 RTC 연결, 오디오 출력, RTC 종료 흐름만 관리한다. |
| [N-04 · ingest_participant_audio](N-04.md) | [B-02](../big-nodes/B-02.md) | B · RTC 수신 서버 | 실제 승인 대상의 RTC 마이크 트랙만 역할별 STT 입력으로 제한한다. |
| [N-05 · transcribe_korean_turn](N-05.md) | [B-02](../big-nodes/B-02.md) | B · STT | 역할별 오디오에서 한국어 발화의 부분·확정 응답을 만든다. |
| [N-06 · finalize_instruction_turn](N-06.md) | [B-02](../big-nodes/B-02.md) | B · 서버 | 중복·부분 발화·역순을 거르고 적용 가능한 문장만 publish한다. |
| [N-07 · open_or_close_projection](N-07.md) | [B-03](../big-nodes/B-03.md) | A · Android | 부모 화면 캡처를 승인 이후 시작하고 시스템 종료에서 정리한다. |
| [N-08 · prepare_frame_for_detection](N-08.md) | [B-03](../big-nodes/B-03.md) | A · Android | 프레임을 현지 분석 가능한 화면으로 준비하며 원본 수명과 좌표 변환을 제한한다. |
| [N-09 · detect_screen_and_targets](N-09.md) | [B-03](../big-nodes/B-03.md) | A · Android, D 검증 | 지원 screen_kind와 현재 보이는 검증 가능한 후보·근거만 반환한다. |
| [N-10 · confirm_screen_transition](N-10.md) | [B-03](../big-nodes/B-03.md) | A · Android | 실제 화면의 의미 변화와 레이아웃 변경을 분리하고 안정된 상태만 발행한다. |
| [N-11 · reduce_help_state](N-11.md) | [B-04](../big-nodes/B-04.md) | D · 제어 서버 | 통화/도움/관찰과 목표 revision을 단일 처리 경로로 갱신한다. |
| [N-12 · record_verified_navigation](N-12.md) | [B-04](../big-nodes/B-04.md) | D · 제어 서버 | 재안내 후보를 만들 수 있도록 실제로 관찰한 경로를 메모리에 남긴다. |
| [N-13 · reconcile_reconnect](N-13.md) | [B-04](../big-nodes/B-04.md) | D · 서버/클라이언트 | 제어 채널 재접속에서 최신 상태와 필요한 재검증만 복원한다. |
| [N-14 · call_zoro_decision](N-14.md) | [B-05](../big-nodes/B-05.md) | C · 추론 adapter | 선택형 Zoro-1 요청을 실제 모델에 보내고 확률·출력·버전 유효성을 검사한다. |
| [N-15 · infer_goal_or_change](N-15.md) | [B-05](../big-nodes/B-05.md) | C · Agent 서버 | 대화에서 단일 목표·세부 조건을 정하고 기존 목표의 변경 여부를 선택한다. |
| [N-16 · enumerate_action_candidates](N-16.md) | [B-05](../big-nodes/B-05.md) | C · Agent 서버, D 프로필 | 현재 화면과 관찰 이력에서 합법적인 다음 행동 후보를 구성한다. |
| [N-17 · choose_single_guide](N-17.md) | [B-05](../big-nodes/B-05.md) | C · Agent 서버 | 목표로 갈 다음 행동 하나를 골라 동작과 유효 범위를 제출한다. |
| [N-18 · judge_goal_arrival](N-18.md) | [B-05](../big-nodes/B-05.md) | C · Agent 서버, D 평가 | 목표에 도착했다고 추정할 수 있는지를 화면 근거와 모델 결정으로 판단한다. |
| [N-19 · gate_guide_on_device](N-19.md) | [B-06](../big-nodes/B-06.md) | A · Android | 3초 타이머와 현재 화면 위치를 기준으로 실제 표시를 허용하거나 차단한다. |
| [N-20 · render_parent_surfaces](N-20.md) | [B-06](../big-nodes/B-06.md) | A · Android | 부모 상단 띠와 실제 화면의 중복 없는 단계 안내를 표시한다. |
| [N-21 · project_role_view](N-21.md) | [B-07](../big-nodes/B-07.md) | D · 서버, B 자녀 UI | 부모·자녀 화면에 각각 허용된 상태를 전달한다. |
| [N-22 · resolve_arrival_or_completion](N-22.md) | [B-07](../big-nodes/B-07.md) | D · 제어 서버 | 도착 추정과 자녀 최종 완료, 부모 안내 끄기를 독립적으로 처리한다. |
| [N-23 · purge_help_and_restart](N-23.md) | [B-07](../big-nodes/B-07.md) | D · 서버, A·B 클라이언트 | 끝난 도움의 관찰과 데이터를 지우고 새 도움은 빈 상태에서 연다. |
| [N-24 · prepare_demo_fixtures](N-24.md) | [B-08](../big-nodes/B-08.md) | D · AVD/자료 | 외부 네트워크와 기기 환경을 포함하여 5개 목표를 재현할 테스트 조건을 고정한다. |
| [N-25 · evaluate_end_to_end](N-25.md) | [B-08](../big-nodes/B-08.md) | D · 리허설/통합, 전체 협업 | 실제 라이브와 모의 시험을 분리하고 요구사항의 통과 여부를 기록한다. |
| [N-26 · track_model_runtime_and_budget](N-26.md) | [B-08](../big-nodes/B-08.md) | C · GPU·배포 | Zoro 런타임 제공과 실제 총비용을 관리한다. |
