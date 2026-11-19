"""N-24 시연 환경 준비 도구 — 실제 AVD 두 대의 상태를 adb로 읽어 로컬 매니페스트를 만든다.

    cd server/session
    python -m hyojason_session.demo_env avd-plan                       # AVD 생성·실행 명령 안내
    python -m hyojason_session.demo_env collect --parent emulator-5554 --child emulator-5556 \
        --out ../../demo/fixtures/local/manifest.json                  # 기기 상태 수집
    python -m hyojason_session.demo_verify prepare ../../demo/fixtures/local/manifest.json --source LIVE
    python -m hyojason_session.demo_env baseline save --serial emulator-5554 \
        --out ../../demo/fixtures/local/baseline-parent.json           # 리허설 전 설정 기록
    python -m hyojason_session.demo_env baseline restore --serial emulator-5554 \
        --file ../../demo/fixtures/local/baseline-parent.json          # 리허설 뒤 원복

원칙 (docs/small-nodes/N-24.md, demo/fixtures/README.md)
- adb로 확인할 수 있는 값만 채운다: Android 버전·이미지·언어·시간대·날짜·네트워크·앱 버전·오버레이 권한.
- 마이크·양방향 음성·화면 공유 동의·로그인·시작 화면·제어 채널은 사람이 확인해야 하므로 False/빈 값으로 둔다.
  N-24 점검(demo_verify prepare)이 이 항목을 막힘으로 보여 주고, 확인한 뒤 로컬 매니페스트를 고친다.
- 계정·전화번호·토큰·서버 주소를 매니페스트에 쓰지 않는다. 결과 파일은 demo/fixtures/local/(git 제외)에 둔다.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .demo_fixtures import _SCENARIOS

# 앱 ID → Android 패키지 (시연 AVD 기본 앱, 효자손은 이슈 #1의 com.hyojason.app)
PACKAGES: dict[str, str] = {
    "hyojason": "com.hyojason.app",
    "settings": "com.android.settings",
    "clock": "com.google.android.deskclock",
    "maps": "com.google.android.apps.maps",
    "sms": "com.google.android.apps.messaging",
    "gmail": "com.google.android.gm",
}
NETWORK_APPS = frozenset({"maps", "gmail"})
EXPECTED_LOCALE = "ko-KR"
EXPECTED_TIME_ZONE = "Asia/Seoul"
ANDROID_API = 35

# 사람이 확인하고 로컬 매니페스트에 직접 고칠 항목
MANUAL_DEVICE_CHECKS = (
    "microphone_verified", "duplex_audio_verified", "audio_route",
    "host_control_reachable", "avd_control_reachable", "capture_allowed",
)
MANUAL_APP_CHECKS = ("start_screens", "observation_allowed", "login_ready")

Runner = Callable[[Sequence[str]], str]

_SAFE = re.compile(r"[^A-Za-z0-9_.+;-]+")


def _subprocess_runner(args: Sequence[str]) -> str:
    done = subprocess.run(list(args), capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise RuntimeError(f"adb failed: {' '.join(args[:4])}")
    return done.stdout


class Adb:
    """`adb -s <serial> shell ...` 래퍼. 테스트에서는 runner를 바꿔 끼운다."""

    def __init__(self, serial: str, runner: Runner = _subprocess_runner, adb: str = "adb") -> None:
        self.serial, self._run, self._adb = serial, runner, adb

    def shell(self, *command: str) -> str:
        return self._run([self._adb, "-s", self.serial, "shell", *command]).strip()

    def prop(self, name: str) -> str:
        return self.shell("getprop", name)


def _safe(text: str) -> str:
    return _SAFE.sub("-", text.strip()).strip("-")[:96] or "unknown"


def android_image(adb: Adb) -> str:
    """같은 이미지인지 비교할 수 있는 식별자: api·abi·빌드 ID (계정·기기 번호 없음)."""
    return _safe(f"api{adb.prop('ro.build.version.sdk')}-{adb.prop('ro.product.cpu.abi')}-{adb.prop('ro.build.id')}")


def app_version(adb: Adb, package: str) -> str | None:
    match = re.search(r"versionName=(\S+)", adb.shell("dumpsys", "package", package))
    return _safe(match.group(1)) if match else None


def network_validated(adb: Adb) -> bool:
    """활성 네트워크가 인터넷 검증(VALIDATED)을 통과했는가."""
    return "VALIDATED" in adb.shell("dumpsys", "connectivity")


def overlay_allowed(adb: Adb, package: str) -> bool:
    return "allow" in adb.shell("appops", "get", package, "SYSTEM_ALERT_WINDOW").lower()


def collect_device(adb: Adb, role: str, device_id: str) -> tuple[dict, list[str]]:
    """DeviceProfile 한 개와 사람이 확인할 항목 목록을 돌려준다."""
    image = android_image(adb)
    online = network_validated(adb)
    locale = adb.prop("persist.sys.locale") or adb.prop("ro.product.locale")
    time_zone = adb.prop("persist.sys.timezone") or None
    apps, manual = [], [f"{device_id}.{name}" for name in MANUAL_DEVICE_CHECKS]
    for app_id, package in PACKAGES.items():
        version = app_version(adb, package)
        if version is None:
            continue
        apps.append({
            "app_id": app_id,
            "version": version,
            # 이슈 #3 합의: profile_version = 앱 versionName + AVD 이미지
            "profile_version": _safe(f"{version};{image}"),
            "start_screens": [],
            "observation_allowed": False,
            "login_ready": app_id not in NETWORK_APPS and app_id != "hyojason",
            "network_ready": online if app_id in NETWORK_APPS else True,
        })
        manual += [f"{device_id}.apps.{app_id}.{name}" for name in MANUAL_APP_CHECKS]
    profile = {
        "device_id": device_id,
        "role": role,
        "source": "LIVE",
        "android_api": int(adb.prop("ro.build.version.sdk") or 0),
        "android_image": image,
        "locale": _safe(locale) if locale else "unknown",
        "time_zone": time_zone,
        "local_date": adb.shell("date", "+%Y-%m-%d") or None,
        "online": online,
        "microphone_verified": False,
        "duplex_audio_verified": False,
        "audio_route": "SPEAKER",
        "host_control_reachable": False,
        "avd_control_reachable": False,
        "capture_allowed": False,
        "overlay_allowed": overlay_allowed(adb, PACKAGES["hyojason"]) if any(
            a["app_id"] == "hyojason" for a in apps) else False,
        "apps": apps,
    }
    return profile, manual


def environment_warnings(profile: dict) -> list[str]:
    """시연 기준과 다른 점 (N-24: 같은 Android 15, 한국어, 시계 시간대)."""
    warnings = []
    if profile["android_api"] != ANDROID_API:
        warnings.append(f"{profile['device_id']}: android_api {profile['android_api']} != {ANDROID_API}")
    if profile["locale"] != EXPECTED_LOCALE:
        warnings.append(f"{profile['device_id']}: locale {profile['locale']} != {EXPECTED_LOCALE}")
    if profile["time_zone"] != EXPECTED_TIME_ZONE:
        warnings.append(f"{profile['device_id']}: time_zone {profile['time_zone']} != {EXPECTED_TIME_ZONE}")
    if not profile["online"]:
        warnings.append(f"{profile['device_id']}: network not validated (지도·Gmail 시나리오 막힘)")
    return warnings


def collect_manifest(parent: Adb, child: Adb, *, fixture_revision: int = 1) -> tuple[dict, list[str], list[str]]:
    """두 AVD에서 로컬 매니페스트를 만든다. 시연 자산은 모두 ready=False로 시작한다(준비한 뒤 고친다)."""
    devices, manual, warnings = [], [], []
    for adb, role, device_id in ((parent, "PARENT", "parent-avd"), (child, "CHILD", "child-avd")):
        profile, checks = collect_device(adb, role, device_id)
        devices.append(profile)
        manual += checks
        warnings += environment_warnings(profile)
    if devices[0]["android_image"] != devices[1]["android_image"]:
        warnings.append("parent/child android_image differ (같은 이미지를 써야 함)")
    assets = [{"scenario_id": sid, "kind": kind, "asset_id": f"demo.{sid.lower()}.{kind}",
               "source": "LIVE", "ready": False}
              for sid, spec in _SCENARIOS.items() for kind in spec.assets]
    manual.append("staged_assets[*].ready (시나리오 자산을 준비한 뒤 true로)")
    manifest = {
        "fixture_revision": fixture_revision,
        "scenario_ids": list(_SCENARIOS),
        "device_profiles": devices,
        "staged_assets": assets,
    }
    return manifest, manual, warnings


# --- 리허설 전후 설정 기록·원복 -------------------------------------------------------

BASELINE_SETTINGS = (("system", "font_scale"), ("system", "screen_off_timeout"))


def save_baseline(adb: Adb) -> dict:
    """SET_FONT·SET_TIMEOUT 리허설이 바꾸는 설정을 기록한다 (N-24: 되돌릴 수 있게 기록)."""
    values = {f"{ns}/{key}": adb.shell("settings", "get", ns, key) for ns, key in BASELINE_SETTINGS}
    density = re.search(r"Override density:\s*(\d+)", adb.shell("wm", "density"))
    return {"settings": values, "density_override": density.group(1) if density else None}


def restore_baseline(adb: Adb, baseline: dict) -> list[str]:
    done = []
    for name, value in baseline["settings"].items():
        ns, key = name.split("/", 1)
        if value in ("", "null"):
            adb.shell("settings", "delete", ns, key)
        else:
            adb.shell("settings", "put", ns, key, value)
        done.append(name)
    if baseline.get("density_override"):
        adb.shell("wm", "density", baseline["density_override"])
    else:
        adb.shell("wm", "density", "reset")
    done.append("wm/density")
    return done


# --- AVD 생성 안내 --------------------------------------------------------------------


def host_abi(machine: str | None = None) -> str:
    machine = (machine or platform.machine()).lower()
    return "arm64-v8a" if machine in ("arm64", "aarch64") else "x86_64"


def avd_plan(abi: str | None = None) -> list[str]:
    """두 AVD를 같은 이미지로 만들고 한국어·서울 시간대로 띄우는 명령. 설치·다운로드는 직접 실행한다."""
    abi = abi or host_abi()
    image = f"system-images;android-{ANDROID_API};google_apis_playstore;{abi}"
    lines = [
        f'sdkmanager "platform-tools" "emulator" "{image}"',
    ]
    for name, port in (("hyojason-parent", 5554), ("hyojason-child", 5556)):
        lines.append(f'avdmanager create avd -n {name} -k "{image}" -d pixel_7')
        lines.append(f"emulator -avd {name} -port {port} -change-locale {EXPECTED_LOCALE} "
                     f"-timezone {EXPECTED_TIME_ZONE}")
    lines += [
        "# 부모 AVD = emulator-5554, 자녀 AVD = emulator-5556",
        "# 문자 시연 수신자는 자녀 AVD 번호(에뮬레이터 포트 기준 1555521 + 5556)를 시연 연락처로 저장한다",
        "# AVD 안에서 호스트 제어 서버는 10.0.2.2:<port> (AVD의 127.0.0.1은 AVD 자신)",
    ]
    if abi != "arm64-v8a":
        lines.append("# 설계 기준은 ARM64(Mac) AVD다. 이 호스트는 x86_64 이미지라 결과에 이미지 차이를 기록한다")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_env", description="N-24 시연 환경 준비 도구")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("avd-plan")
    plan.add_argument("--abi", choices=("arm64-v8a", "x86_64"))
    col = sub.add_parser("collect")
    col.add_argument("--parent", required=True)
    col.add_argument("--child", required=True)
    col.add_argument("--out", required=True)
    col.add_argument("--fixture-revision", type=int, default=1)
    base = sub.add_parser("baseline")
    base.add_argument("action", choices=("save", "restore"))
    base.add_argument("--serial", required=True)
    base.add_argument("--out")
    base.add_argument("--file")
    args = parser.parse_args(argv)

    if args.command == "avd-plan":
        print("\n".join(avd_plan(args.abi)))
        return 0
    if args.command == "collect":
        manifest, manual, warnings = collect_manifest(Adb(args.parent), Adb(args.child),
                                                      fixture_revision=args.fixture_revision)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
        for line in warnings:
            print(f"warning: {line}")
        print("사람이 확인한 뒤 고칠 항목:")
        for line in manual:
            print(f"  - {line}")
        return 0
    adb = Adb(args.serial)
    if args.action == "save":
        if not args.out:
            parser.error("--out is required for save")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(save_baseline(adb), indent=2) + "\n", encoding="utf-8")
        print(f"saved {args.out}")
        return 0
    if not args.file:
        parser.error("--file is required for restore")
    restored = restore_baseline(adb, json.loads(Path(args.file).read_text(encoding="utf-8")))
    print("restored:", ", ".join(restored))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
