#!/usr/bin/env bash
# Mac에서 통합 백엔드(server.mvp)를 LiveKit·RTZR 키와 Zoro-1 연결로 띄운다. 개인 테스트용.
# 사용: (저장소 루트에서) bash server/ops/run_local_backend.sh
# 전제: 다른 터미널에서 `brev port-forward hyojason-zoro -p 9000:30001` 실행 중.
#       VM 백엔드용 `-p 8000:18002`는 꺼 둔다(8000 포트 충돌).
set -euo pipefail
cd "$(dirname "$0")/../.."
ENV_FILE=server/voice/.env

if [[ ! -f $ENV_FILE ]]; then
  cp server/voice/.env.example "$ENV_FILE"
  echo "[준비] $ENV_FILE 를 만들었어요. 열어서 LiveKit·RTZR 키를 채운 뒤 다시 실행하세요. (커밋 금지, .gitignore 등록됨)"
  exit 1
fi
set -a; . "$ENV_FILE"; set +a

missing=()
for k in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET RTZR_CLIENT_ID RTZR_CLIENT_SECRET; do
  v=${!k:-}
  [[ -z $v || $v == *"<project>"* ]] && missing+=("$k")
done
if (( ${#missing[@]} )); then
  echo "[키 없음] $ENV_FILE 에 비어 있는 값: ${missing[*]}"
  echo "         LiveKit 3개가 없으면 통화가 안 되고(rtc-token DEPENDENCY_UNAVAILABLE), RTZR 2개가 없으면 음성 인식이 안 돼요."
  [[ " ${missing[*]} " == *" LIVEKIT_"* ]] && exit 1
  echo "         (LiveKit은 있으니 통화만 되는 상태로 계속 진행합니다)"
fi
echo "[키] LiveKit·RTZR 값 확인 (값은 출력하지 않음)"

export ZORO_DECIDE_URL=${ZORO_DECIDE_URL:-http://localhost:9000/v1/decide}
export ZORO_MODEL_REVISION=${ZORO_MODEL_REVISION:-bf1b76b9b4df03b05fcbb73444e1cd4db0f566da}
code=$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$ZORO_DECIDE_URL" || true)
if [[ $code == 000 ]]; then
  echo "[경고] Zoro-1($ZORO_DECIDE_URL)에 연결 안 됨. 다른 터미널에서 brev port-forward hyojason-zoro -p 9000:30001 을 켜세요."
else
  echo "[Zoro-1] $ZORO_DECIDE_URL 응답 확인 (HTTP $code), revision $ZORO_MODEL_REVISION"
fi

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[포트] 8000이 이미 사용 중이에요. brev port-forward ... -p 8000:18002 나 이전 서버를 끄고 다시 실행하세요."
  lsof -nP -iTCP:8000 -sTCP:LISTEN | tail -n +2
  exit 1
fi

if [[ ! -x server/voice/.venv/bin/python ]]; then
  echo "[venv] server/voice/.venv 생성 + 패키지 설치 (처음 한 번, 몇 분 걸림)"
  python3 -m venv server/voice/.venv
  server/voice/.venv/bin/python -m pip install -q -r server/voice/requirements.txt 'uvicorn[standard]==0.54.0'
fi

echo "[서버] http://127.0.0.1:8000  (에뮬레이터는 10.0.2.2:8000)  — 끄려면 Ctrl+C"
PYTHONPATH=.:server/session:server/voice exec server/voice/.venv/bin/python -m uvicorn server.mvp:create_app \
  --factory --host 127.0.0.1 --port 8000
