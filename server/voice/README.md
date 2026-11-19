# server/voice · 통화·한국어 STT (B)

B-01(N-01~03)과 B-02(N-04~06)의 서버 쪽. 계획은 [`docs/B-dev-plan.md`](../../docs/B-dev-plan.md).

공통 계약(NodeResult·Basis·Role·Event 봉투)은 새로 만들지 않고 `server/session`(D)의 `hyojason_session`을 import한다(이슈 #2: 서버 Python, 필드명은 docs와 1:1).

## 실행 (Python 3.11+, 가상환경 권장)

공통 계약은 D의 `server/session/hyojason_session`에 있다. D 브랜치가 main에 합쳐지기 전에는 D를 옆 폴더에 두고 `PYTHONPATH`로 연결한다.

```bash
git worktree add --detach ../Hyojason-D origin/D       # 레포 루트에서 1회
cd server/voice
pip install -r requirements.txt
export PYTHONPATH="$(cd ../../../Hyojason-D/server/session && pwd)"   # 셸마다
cp .env.example .env                                   # 값은 직접 채움. 커밋 금지
python -m pytest
set -a; . ./.env; set +a                               # .env를 셸 환경변수로 (셸마다)
uvicorn hyojason_voice.app:app --reload --port 8000    # 에뮬레이터에서는 http://10.0.2.2:8000
```

D가 main에 합쳐지면 `PYTHONPATH` 설정과 worktree가 필요 없다(`pyproject`의 `pythonpath`에 `../session`이 있음).

## 구조

| 파일 | 노드 | 상태 |
|---|---|---|
| `pair.py` | N-01 일회용 초대 코드, D `PairDirectory`(role_of) 구현 | FIXTURE |
| `consent.py` | N-02 AI 동의와 부모 기기 권한을 기능별로 계산 | FIXTURE |
| `call.py` | N-03 서버 쪽 발신·수락·참가·종료. 스피커 전환은 Android | FIXTURE |
| `audio.py` | N-04 승인 마이크 트랙만 역할·통화별 메모리 큐 | FIXTURE |
| `stt.py` | N-05 엔진 결과 → 계약 변환 (엔진 미연결) | FIXTURE |
| `finalize.py` | N-06 중복·역순·종료 후 결과 차단, 자녀 발화만 triggered | FIXTURE |
| `events.py` | `call.invite/accepted/connected/ended`, `speech.final` 봉투 | — |
| `app.py` | 제어 엔드포인트 + dev 텍스트 입력 스텁 | FIXTURE |
| `worker.py` | 수락 때 자동 실행. 참가·퇴장 → N-03, 역할별 마이크 → N-04 → RTZR → N-05 → N-06 | LIVE 확인 전 |

## STT worker

통화가 수락되면 서버가 그 방에 worker를 자동으로 띄운다(서버 셸에 `.env`가 올라가 있어야 함. 없으면 경고만 찍고 음성만).
worker가 방에서 본 참가·퇴장이 N-03 JOINED/END가 되고, 확정문이 N-06을 거쳐 `speech.final`(source=`stt`)이 된다.

```bash
scripts/dev_call.sh                 # 새 페어·양쪽 동의·통화·수락 → 부모/자녀 링크 출력
curl -sS localhost:8000/v1/dev/outbox
```

서버 상태를 안 바꾸고 인식만 볼 때(원문 출력은 dev 전용):
`PAIR_ID=<PID> .venv/bin/python -m hyojason_voice.worker <call_id> --print-text [--no-keywords]`

## 텍스트 입력 스텁 (C·D용)

```bash
curl -X POST localhost:8000/v1/dev/speech-final -H 'X-Pair-Id: <pair_id>' -H 'Content-Type: application/json' \
  -d '{"call_id":"<call_id>","speaker_role":"CHILD","text":"디스플레이 눌러 줘"}'
curl localhost:8000/v1/dev/outbox      # 발행된 이벤트 (source=manual_test)
```

## 아직 없는 것 (API 키 또는 팀 결정 필요)

- LiveKit 웹훅: 참가 콜백은 `/v1/dev/calls/{id}/joined`로 대신. (`rtc-token`은 구현됨. `.env`에 LiveKit 키가 없으면 503)
- STT worker(LiveKit Agents, 트랙별 스트림)와 RTZR(`sommers_ko`) 어댑터: 키 받은 뒤 `SttEvent`를 만들어 `transcribe_korean_turn`에 넘기면 된다.
- 실제 인증: 지금은 `X-Actor-Id`/`X-Pair-Id` 헤더를 그대로 믿는다(dev 전용).
- D로 보내는 WS: D의 `WS /v1/events`가 생기면 `app._publish`만 바꾼다.
- Android `rtc`/`child`: 이슈 #1 골격 PR(`com.hyojason.app`, SDK 35, `android/contract`) 이후 시작.

## D와 맞출 것

- 가족 역할은 앱 토글로 고른다. `POST /v1/pairs/invite`·`/claim` body의 `role`이 그 값이다. 발신은 누구나, 수락은 상대만(#6). 안내 대상(`assisted_role`)은 아직 없음.

- LiveKit identity `{pair_id}:{role}` (`common.identity`).
- `speech.final` payload: `utterance_id, utterance_seq, speaker_role, text, call_id, source`.
- `call.*` payload: `call_id, role, reason`.
- `trace_id`(#5): B가 발신(`POST /v1/calls`) 때 통화마다 하나 만들어 그 통화의 N-03~N-06 결과·`call.*`·`speech.final`·통화 중 동의 변경에 같이 쓴다. 모든 응답에 `trace_id`를 실어 앱도 받아 쓸 수 있다. D가 발급하기로 정하면 `call.new_trace_id`만 바꾼다.
- 동의 버전: 앱은 마지막으로 본 `consent_revision`을 보내고 서버가 올린다(D의 basis 방식과 같음). 어긋나면 409 `STALE_BASIS` → `GET /v1/consent`로 재조회.
- 동의 버전은 부모 기기 권한(캡처·표시)만 바뀌어도 오른다 → 그 사이 진행 중이던 STT 확정문 1건은 버려진다(보수적).
- D의 `NodeRun`에 `partial()`이 없어 `common.partial`로 대신 쓰는 중.
