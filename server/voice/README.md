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
| `service.py` | **D가 부를 B 판단 함수 묶음**(#10). 인증(`authenticate`·`role_of`), N-01 `invite`·`claim`·`unpair`, N-02, N-03 `call_op`·`rtc_token`, 웹훅, 이벤트 출구 `set_event_sink`. FastAPI 없음 | FIXTURE + AVD LIVE |
| `auth.py` | 기기 Bearer 토큰 발급·확인 | FIXTURE + AVD LIVE |
| `app.py` | `service.py`를 감싼 B 임시 라우트(D 연결 전 dev·AVD용). 판단 로직 없음 | — |
| `worker.py` | 수락 때 자동 실행. 참가·퇴장 → N-03, 역할별 마이크 → N-04 → 잡음 제거 → RTZR → N-05 → N-06 | LIVE 확인 전 |
| `denoise.py` | STT 앞 배경 잡음 제거(GTCRN 스트리밍 ONNX). 모델은 `models/` | FIXTURE |

## STT worker

통화가 수락되면 서버가 그 방에 worker를 자동으로 띄운다(서버 셸에 `.env`가 올라가 있어야 함. 없으면 경고만 찍고 음성만).
worker가 방에서 본 참가·퇴장이 N-03 JOINED/END가 되고, 확정문이 N-06을 거쳐 `speech.final`(source=`stt`)이 된다.

```bash
scripts/dev_call.sh                 # 새 페어·양쪽 동의·통화·수락 → 부모/자녀 링크 출력
curl -sS localhost:8000/v1/dev/outbox
```

서버 상태를 안 바꾸고 인식만 볼 때(원문 출력은 dev 전용):
`PAIR_ID=<PID> .venv/bin/python -m hyojason_voice.worker <call_id> --print-text [--no-keywords] [--no-denoise]`

## STT 앞 배경 잡음 제거

worker는 동의·트랙 검사(N-04)를 통과한 마이크 프레임만 RTZR로 보낸다. 그 전에 트랙마다 배경 잡음을 줄인다. 다른 사람의 목소리는 거르지 않는다.

- 모델: [GTCRN](https://github.com/Xiaobin-Rong/gtcrn) 스트리밍 ONNX(DNS3 학습, MIT, 535 KB, `models/LICENSE-GTCRN`). 이 모델을 고른 이유: onnxruntime과 numpy만 있으면 돌고 프레임 단위로 처리된다. DeepFilterNet은 `numpy<2`·`packaging<24`를 요구해 현재 `requirements.txt`와 충돌한다.
- 흐름: 48 kHz 프레임 → LiveKit `AudioResampler`로 16 kHz → 16ms마다 모델 1회 → RTZR. 지연은 32ms이고, 처리 시간은 한 스트림에 16ms당 약 2.5ms(CPU 1스레드)다. 모델 실행은 이벤트 루프 밖 스레드에서 한다.
- 설정: `HYOJASON_VOICE_DENOISE=off`로 끄고, `HYOJASON_VOICE_DENOISE_MIX=0.8`처럼 원래 소리를 섞을 수 있다(기본 1.0). 모델을 못 올리거나 통화 중 오류가 나면 경고만 남기고 원래 소리를 보낸다.
- PCM과 모델 상태는 메모리에만 두고 트랙이 끝나면 버린다(B02.2).

오프라인 평가(16 kHz mono wav 폴더, 잡음 폴더를 주지 않으면 합성 배경 잡음 5종):

```bash
python scripts/eval_denoise.py <깨끗한 음성 폴더> [--noise-dir <잡음 폴더>] [--snr 0 5 10]
```

한국어 TTS 6문장(시연 지시문)에 합성 잡음 5종을 SNR 0/5/10 dB로 섞어 SI-SDR을 쟀다. 평균 +10.3 dB, 최저 +3.6 dB(선풍기 험, 0 dB)로 모든 조건에서 좋아졌다. 이 결과는 FIXTURE다. RTZR 인식률 변화는 실제 통화에서 `--no-denoise`와 비교해 확인해야 한다(미검증).

## 앱 폴링

`GET /v1/calls/current`: 페어의 진행 중 통화(`call_id, call_state, caller_role, accepted, connected_roles`) 또는 `null`.
앱(`android/child`)이 화면이 보이는 동안 1.5초마다 물어 수신·수락·상대 끊음을 안다. D의 `WS /v1/events`가 생기면 이벤트로 바꾼다.

## D 서버에 연결하기 (#10)

D 서버가 B 함수를 그대로 부른다. `origin/D`(e92ef7b)에서 아래처럼 바꿔 끼워 확인했다(help/current·발신·수락·WS 인증 통과, 가짜 토큰 401).

```python
from hyojason_voice import service
app = create_app(ServerConfig(authenticator=service, service_token=...))  # MockTokenAuthenticator 자리
service.set_event_sink(lambda trace_id, ev: ...)  # B 이벤트(call.*, speech.final)를 D 쪽으로
```

가족 연결은 통화가 끝나도 유지되고 `POST /v1/pairs/unpair`(앱 '가족 연결 끊기')로만 끊긴다. 끊기면 두 기기 모두 다음 요청부터 403 → 연결 화면.

## 인증 (#10)

`/v1/pairs/invite`·`/claim`에 토큰 없이 오면 서버가 새 기기 토큰을 응답의 `token`으로 준다. 이후 모든 요청은 `Authorization: Bearer <token>`. 페어·역할은 서버가 토큰에서 찾는다(`X-Actor-Id`/`X-Pair-Id`는 없어짐). `AUTH_OUT=파일 scripts/dev_call.sh`로 dev 토큰을 파일에 받을 수 있다.

## 텍스트 입력 스텁 (C·D용)

```bash
curl -X POST localhost:8000/v1/dev/speech-final -H 'Authorization: Bearer <기기 토큰>' -H 'Content-Type: application/json' \
  -d '{"call_id":"<call_id>","speaker_role":"CHILD","text":"디스플레이 눌러 줘"}'
curl localhost:8000/v1/dev/outbox      # 발행된 이벤트 (source=manual_test)
```

## 아직 없는 것 (API 키 또는 팀 결정 필요)

- LiveKit 웹훅: 참가 콜백은 `/v1/dev/calls/{id}/joined`로 대신. (`rtc-token`은 구현됨. `.env`에 LiveKit 키가 없으면 503)
- STT worker(LiveKit Agents, 트랙별 스트림)와 RTZR(`sommers_ko`) 어댑터: 키 받은 뒤 `SttEvent`를 만들어 `transcribe_korean_turn`에 넘기면 된다.
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
