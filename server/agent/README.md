현재 담당: Codex (2026-11-19)

# B-05 판단 파이프라인

`B05Workflow.run()`은 세션 서버가 적용한 최신 상태와 이벤트를 받아 판단 결과를 돌려준다. 통화 중에는 자녀 발화, 대상 위치, 조작 수행을 처리한다. 통화 후에는 목표별 완료 단서를 먼저 보고, 필요하면 다음 행동 하나를 제안한다. 3초 타이머와 표시 적용은 부모 앱이 맡는다. [B-05 설계](../../docs/big-nodes/B-05.md)

## 연결

1. D가 이벤트를 적용해 얻은 상태·방문 이력을 `from_session(snapshot, trail, processing_allowed=...)`로 변환한다. `processing_allowed`는 현재 양쪽 동의와 활성 처리권을 확인한 세션 서버의 값이다. 원본 D 스냅샷의 `basis`를 사용하면 발화 순서와 종료 전후 버전도 보존된다.
2. `B05Input`과 최신 상태를 다시 읽는 `read_current`를 `B05Workflow.run()`에 전달한다. `speech.final`은 `speech`를, 검증된 의미 화면 전이는 `observation.transition`과 직전 `before` 화면을 사용한다. 같은 의미의 스크롤은 최신 `target_ids`와 함께 `screen.observed`로 평가할 수 있다. 도움 종료 뒤 목표가 남았으면 먼저 새 안정 화면을 수락한다.
3. `on_instruction`으로 도착한 초안은 모델의 목표 판단을 기다리기 전에 `instruction.drafted`로 적용한다. 최종 `instruction_draft`는 같은 초안의 결과 기록이다. `goal_draft`는 `goal.drafted`로 적용한다. 이전 초안과의 중복 제거 및 최종 커밋은 D가 소유한다.
4. 위치·수행·복귀·다음 행동·도착은 `B05Output`으로 반환된다. 제안의 최종 적용 전에 최신 Basis를 다시 확인한다. `NodeResult.diagnostics.source`와 모델 호출 자체의 출처 둘 다 확인한다.

## 화면과 근거

화면 종류와 대상 ID는 [이슈 #3의 A·D 합의](https://github.com/Bungeo-BeanOrCream/Hyojason/issues/3#issuecomment-7006659871)를 따른다. 실제 AVD 검증이 필요한 항목은 아래와 같다.

| 쓰임 | 관찰 근거 |
|---|---|
| 화면 아래 | 같은 영역의 `scroll_down:<container>`와 `below_fold:<container>:<target_id>`가 모두 필요하다. 이 근거 표현은 시연 프로필의 제안이며 A·D의 실제 공급 확인이 필요하다 |
| 수행 판정 | 검증된 화면 전이의 출발 화면에서 등록 대상이 보이고 전이 목적지가 유일해야 한다. 같은 화면의 의미 상태 변경은 `activated:<target_id>` 근거를 사용한다 |
| 결과 이벤트 | D 카탈로그가 B-05 결과 코드 `visible_target`, `observed:<target_id>` 및 스크롤·복귀 근거를 허용해야 한다 |
| 알람 목표 | `alarm_card_active`와 같은 카드의 `alarm_next:<today|tomorrow>:<HH:MM>`를 확인한다. 예시는 `alarm_next:tomorrow:06:00`이다. 저장 버튼 후보는 `alarm_edit:<today|tomorrow>:<HH:MM>` 근거가 필요하다 |
| 나머지 목표 | 시연 프로필의 설정 10분, 지도 시연 장소, 승인된 문자 상대·문구, 준비한 Gmail 항목을 사용한다. 시연 앱별 텍스트와 증거는 실제 AVD에서 검증한다 |

## Zoro-1 연결과 검증

`B05Workflow()`는 `ZORO_DECIDE_URL`에 지정한 공식 `/v1/decide` 서버와 `ZORO_MODEL_REVISION`에 지정한 배포 버전을 사용한다. 인증된 서빙 프록시에는 `ZORO_DECIDE_TOKEN`을 환경에서 제공한다. 서버가 버전을 회신하면 지정 버전과 일치하는지 검사한다. 서빙 프로세스와 checkpoint는 GPU 운영 담당이 고정한다. [공식 호출 형식](https://huggingface.co/trillionlabs/Zoro-1#using-the-typed-decision-head)

저장소 루트에서 실행한다.

~~~sh
python3.11 -B -m unittest discover -s server/agent/tests -v
~~~

B05.1–B05.5, E07·E08·E11·E14, N-14·N-16의 주요 분기를 합성 입력으로 검사한다. D 브랜치의 세션 실코드도 합성 모델 응답 경로로 교차 검증했다. 실제 L40S endpoint에서 다섯 판단 실측과 Android AVD에서의 오디오·표시는 미검증 상태다.
