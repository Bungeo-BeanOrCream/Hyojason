현재 담당: Codex (2026-11-19)

# N-26 · Zoro-1과 크레딧 점검

N-26은 N-14에서 동일 모델 버전의 실제 판단 다섯 종류를 확인하고, 누적 크레딧 차감액에 따라 준비 상태를 반환한다. 고정 테스트 문장을 보내더라도 실제 서버에서 돌아온 응답은 모델 출처로 구분한다. 모의 응답은 `FIXTURE`로 남긴다. [N-26 계약](../../docs/small-nodes/N-26.md)

## 실모델 확인

서빙 콘솔에서 GPU와 현재까지 차감된 금액을 확인한다. 비용에는 청구된 스토리지·전송 등 부대비용을 포함한다. GPU는 N-25의 V-08 판정에서 L40S를 기대한다. 실행 환경에 `ZORO_DECIDE_URL`과 `ZORO_MODEL_REVISION`을 설정하고, 인증 프록시가 있는 경우 환경의 `ZORO_DECIDE_TOKEN`을 사용한다. 운영자가 확인한 누적 금액을 센트로 `HYOJASON_SPEND_CENTS`에 설정한다. 최초 시도 파일은 보관하고 실행마다 새 출력 경로를 사용한다.

~~~sh
python3.11 -m server.ops.runtime_smoke \
  --gpu-sku L40S \
  --spend-cents "$HYOJASON_SPEND_CENTS" \
  --evidence-json /tmp/hyojason-v08.json
~~~

누적 $70 이상에선 새 smoke 요청을 보내지 않는다. 누적 $100은 예산 소진이고 초과 기록은 오류다. 기존 다섯 실제 결과를 판정하는 경우 모델이 준비되어 있어도 새 실험에 대해 `partial/budget_hold`를 반환한다. `--experiments-paused-at-70`는 실제 중지 여부를 확인한 뒤에만 지정한다.

증거 JSON에는 `observed_runs` 배열로 `V08-GOAL`, `V08-TARGET_LOCATION`, `V08-ACTION_MATCH`, `V08-NEXT_ACTION`, `V08-ARRIVAL`, `V08-budget` 기록이 들어간다. D의 평가기는 누적 차감액을 `credits_cents` 필드로 읽는다. D 브랜치에서는 `demo_verify evaluate <fixtures-dir> <evidence-file> --source LIVE`로 읽을 수 있다. 리허설 결과와 합칠 때 각 케이스의 첫 시도를 보존한다.

## V-05 대화형 목표 평가

[대화형 목표 표본](../../demo/fixtures/goal_conversations.json) 12개를 N-15에 발화 순서대로 넣는다. 세션 서버와 같은 방식으로 최근 확정 발화 창, 직전 GoalBelief, 현재 목표를 다음 발화에 넘긴다. 대화 중 목표 변경, 잡담 뒤 유지, 목표 없음, 슬롯 보충, 자녀가 지원 대상인 도움, 미지원·위험 요청을 포함한다. 위 실모델 확인과 같은 환경 변수와 비용 기준을 쓴다. 누적 $70 이상이면 요청을 보내지 않고 종료 코드 2를 반환한다.

~~~sh
python3.11 -m server.ops.goal_conversation_eval \
  --spend-cents "$HYOJASON_SPEND_CENTS" \
  --evidence-json /tmp/hyojason-v05-goal.json
~~~

결과에는 케이스·발화 순번·화자·모델 선택·확률·지연·출처와 그 발화 뒤 목표만 남고 발화 원문은 남지 않는다. 요약은 최종 목표 일치 수, 발화별 목표 일치 수, GOAL 호출 수, `TIMEOUT`·오류 수, 지연 p50/p95, `failed_cases`다. 실행 전후 크레딧 차감액을 함께 기록한다.

## 회귀 검증

~~~sh
python3.11 -B -m unittest discover -s server/ops/tests -v
python3.11 -B -m unittest discover -s server/agent/tests -v
~~~

C 코드와 D 브랜치의 N-24·N-25 평가기를 함께 검증한다. B08.2의 실제 요청 timeout과 모의 응답 구분, B08.3의 미검증 증거, B08.4의 $70·$100 경계를 확인한다. 실제 엔드포인트와 비용 장부가 준비되면 위 절차로 라이브 증거를 수집한다.
