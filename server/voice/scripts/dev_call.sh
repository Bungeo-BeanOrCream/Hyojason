#!/usr/bin/env bash
# dev 전용: 새 페어 → 통화 → 수락 → 부모·자녀 토큰을 받아 meet.livekit.io 링크를 출력한다.
# 매번 새 actor 이름을 써서 서버를 재시작하지 않아도 다시 돌릴 수 있다.
# 사용: server/voice/scripts/dev_call.sh   (서버가 localhost:8000에 떠 있어야 함)
set -euo pipefail
B=${B:-localhost:8000}
J='Content-Type: application/json'
A=$RANDOM$RANDOM

step() {  # step <이름> <curl 인자...> : 실패하면 단계와 응답을 보여 주고 멈춘다
  local name=$1; shift
  local out code
  out=$(curl -sS -w '\n%{http_code}' "$@") || { echo "[$name] 서버 연결 실패 (터미널 1 서버 확인)" >&2; exit 1; }
  code=${out##*$'\n'}; out=${out%$'\n'*}
  [[ $code == 2* ]] || { echo "[$name] HTTP $code: $out" >&2; exit 1; }
  printf '%s' "$out"
}
get() { python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }

INV=$(step invite -XPOST "$B/v1/pairs/invite" -H "X-Actor-Id: P$A" -H "$J" -d '{"role":"PARENT"}')
PID=$(get '["pair_id"]' <<<"$INV"); CODE=$(get '["invite_code"]' <<<"$INV")
step claim -XPOST "$B/v1/pairs/claim" -H "X-Actor-Id: C$A" -H "$J" \
  -d "{\"role\":\"CHILD\",\"invite_code\":\"$CODE\"}" >/dev/null

P=(-H "X-Actor-Id: P$A" -H "X-Pair-Id: $PID"); C=(-H "X-Actor-Id: C$A" -H "X-Pair-Id: $PID")
CID=$(step call -XPOST "$B/v1/calls" "${P[@]}" | get '["data"]["call_id"]')
step accept -XPOST "$B/v1/calls/$CID/accept" "${C[@]}" >/dev/null

TPJ=$(step token-parent -XPOST "$B/v1/calls/$CID/rtc-token" "${P[@]}")
TCJ=$(step token-child -XPOST "$B/v1/calls/$CID/rtc-token" "${C[@]}")
URL=$(get '["url"]' <<<"$TPJ")

echo "CID=$CID"
echo "부모: https://meet.livekit.io/custom?liveKitUrl=$URL&token=$(get '["token"]' <<<"$TPJ")"
echo "자녀: https://meet.livekit.io/custom?liveKitUrl=$URL&token=$(get '["token"]' <<<"$TCJ")"
echo "worker: .venv/bin/python -m hyojason_voice.worker $CID --print-text"
