#!/usr/bin/env bash
# N-24 시연 환경 준비 — 팀 Apple Silicon Mac에서 시연 AVD 두 대(부모·자녀)를 만들고 상태를 수집한다.
#
#   bash demo/fixtures/setup_avd_mac.sh        (저장소 루트에서)
#
# 결정: docs/work/D-status.md 결정 메모 11 (시연은 팀 Mac, ARM64 AVD).
# 명령은 `python -m hyojason_session.demo_env avd-plan --abi arm64-v8a`와 같다(테스트로 일치 확인).
# Android 시스템 이미지·에뮬레이터를 내려받으므로(수 GB) 실행하는 사람이 확인하고 돌린다.
set -euo pipefail

ANDROID_API=35
IMAGE="system-images;android-${ANDROID_API};google_apis_playstore;arm64-v8a"
LOCALE="ko-KR"
TIME_ZONE="Asia/Seoul"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
TOOLS="$SDK/cmdline-tools/latest/bin"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "Apple Silicon Mac에서 실행하세요 (설계 기준 ARM64 AVD)." >&2
  exit 1
fi
if [ ! -x "$TOOLS/sdkmanager" ]; then
  echo "Android SDK command-line tools가 없습니다." >&2
  echo "Android Studio > Settings > Android SDK > SDK Tools에서 'Android SDK Command-line Tools'를 설치하세요." >&2
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "Python 3.11 이상이 필요합니다 (server/session)." >&2
  exit 1
fi
export PATH="$TOOLS:$SDK/platform-tools:$SDK/emulator:$PATH"

echo "[1/4] SDK 구성요소 설치 확인"
sdkmanager --install "platform-tools" "emulator" "$IMAGE"

echo "[2/4] AVD 만들기 (이미 있으면 그대로 둔다)"
for name in hyojason-parent hyojason-child; do
  if avdmanager list avd -c | grep -qx "$name"; then
    echo "  $name: 이미 있음"
  else
    echo no | avdmanager create avd -n "$name" -k "$IMAGE" -d pixel_7
  fi
done

echo "[3/4] AVD 실행 (부모 emulator-5554, 자녀 emulator-5556)과 부팅 대기"
start_avd() {
  nohup emulator -avd "$1" -port "$2" -change-locale "$LOCALE" -timezone "$TIME_ZONE" >/dev/null 2>&1 &
}
adb devices | grep -q "^emulator-5554" || start_avd hyojason-parent 5554
adb devices | grep -q "^emulator-5556" || start_avd hyojason-child 5556
for serial in emulator-5554 emulator-5556; do
  adb -s "$serial" wait-for-device
  until [ "$(adb -s "$serial" shell getprop sys.boot_completed | tr -d '\r')" = "1" ]; do
    sleep 2
  done
  echo "  $serial 부팅 완료"
done

echo "[4/4] 상태 수집 → demo/fixtures/local/manifest.json (git 제외)"
cd "$ROOT/server/session"
python3 -m hyojason_session.demo_env collect --parent emulator-5554 --child emulator-5556 \
  --out "$ROOT/demo/fixtures/local/manifest.json"

cat <<'NOTE'

다음 단계 (demo/fixtures/README.md '실제 AVD 준비 도구')
- 위의 '사람이 확인한 뒤 고칠 항목'을 확인하고 demo/fixtures/local/manifest.json을 고친다.
- 두 AVD가 같은 Mac 마이크를 함께 들으므로, 역할별 음소거나 분리한 오디오 장치를 B와 정한다(work-plan).
- 지도·Gmail은 Play 스토어 이미지에서 시연 계정으로 로그인한다. 계정 정보는 저장소에 쓰지 않는다.
- 리허설 전 설정 기록: python3 -m hyojason_session.demo_env baseline save --serial emulator-5554 \
    --out ../../demo/fixtures/local/baseline-parent.json   (자녀도 같은 방법)
- 점검: python3 -m hyojason_session.demo_verify prepare ../../demo/fixtures/local/manifest.json --source LIVE
NOTE
