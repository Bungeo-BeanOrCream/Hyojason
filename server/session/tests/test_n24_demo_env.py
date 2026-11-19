"""N-24 시연 환경 준비 도구 — 가짜 adb로 수집·원복·AVD 안내를 확인한다.

실제 AVD에서의 확인은 하지 않았다(UNVERIFIED). 수집기 출력은 Codex의 N-24 점검기
(load_demo_manifest, prepare_demo_fixtures)에 그대로 넣어 본다.
"""

from __future__ import annotations

import copy
import json

from hyojason_session.common.enums import Source
from hyojason_session.demo_env import (
    PACKAGES, Adb, avd_plan, collect_manifest, host_abi, main, restore_baseline, save_baseline,
)
from hyojason_session.demo_fixtures import _SCENARIOS, load_demo_manifest, prepare_demo_fixtures

ALL_APPS = {pkg: "1.0.0" for pkg in PACKAGES.values()}


def fake_device(*, sdk="35", abi="arm64-v8a", build="AP3A.240905.015", locale="ko-KR",
                tz="Asia/Seoul", date="2026-11-20", validated=True, packages=None, overlay="allow"):
    packages = ALL_APPS if packages is None else packages
    props = {"ro.build.version.sdk": sdk, "ro.product.cpu.abi": abi, "ro.build.id": build,
             "persist.sys.locale": locale, "ro.product.locale": locale, "persist.sys.timezone": tz}
    calls = []

    def runner(args):
        assert list(args[:2]) == ["adb", "-s"] and args[3] == "shell"
        cmd = " ".join(args[4:])
        calls.append(cmd)
        if cmd.startswith("getprop "):
            return props.get(cmd.split()[1], "")
        if cmd == "date +%Y-%m-%d":
            return date
        if cmd == "dumpsys connectivity":
            return "NetworkAgentInfo{ ni{WIFI} Capabilities: INTERNET&VALIDATED }" if validated else "Capabilities: INTERNET"
        if cmd.startswith("dumpsys package "):
            pkg = cmd.split()[2]
            return f"Packages:\n  Package [{pkg}]\n    versionName={packages[pkg]}\n" if pkg in packages else "Unable to find package"
        if cmd.startswith("appops get"):
            return f"SYSTEM_ALERT_WINDOW: {overlay}; time=+1m"
        if cmd.startswith("settings get system font_scale"):
            return "1.0"
        if cmd.startswith("settings get system screen_off_timeout"):
            return "30000"
        if cmd == "wm density":
            return "Physical density: 420"
        if cmd.startswith(("settings put", "settings delete", "wm density ")):
            return ""
        raise AssertionError(cmd)

    runner.calls = calls
    return runner


def collect(**child_overrides):
    return collect_manifest(Adb("emulator-5554", fake_device()), Adb("emulator-5556", fake_device(**child_overrides)))


def prepare(manifest, source=Source.LIVE):
    m = load_demo_manifest(manifest)
    return prepare_demo_fixtures(m.device_profiles, m.fixture_revision, m.scenario_ids, m.staged_assets,
                                 source=source, trace_id="trace-n24")


def confirm_manually(manifest):
    """운영자가 실제로 확인한 뒤 로컬 매니페스트를 고치는 단계를 흉내 낸다."""
    m = copy.deepcopy(manifest)
    screens: dict[str, set] = {}
    for spec in _SCENARIOS.values():
        screens.setdefault(spec.app_id, set()).update(spec.start_screens)
    for device in m["device_profiles"]:
        device.update(microphone_verified=True, duplex_audio_verified=True, host_control_reachable=True,
                      avd_control_reachable=True, capture_allowed=True)
        for app in device["apps"]:
            app.update(start_screens=sorted(screens.get(app["app_id"], ())), observation_allowed=True,
                       login_ready=True)
    for asset in m["staged_assets"]:
        asset["ready"] = True
    return m


def test_24_collect_fills_adb_facts_and_leaves_manual_checks_blocked():
    manifest, manual, warnings = collect()
    assert warnings == []
    parent = manifest["device_profiles"][0]
    assert (parent["role"], parent["source"], parent["android_api"], parent["locale"], parent["time_zone"]) == (
        "PARENT", "LIVE", 35, "ko-KR", "Asia/Seoul")
    assert parent["android_image"] == "api35-arm64-v8a-AP3A.240905.015"
    assert parent["overlay_allowed"] is True and parent["online"] is True
    assert {a["app_id"] for a in parent["apps"]} == set(PACKAGES)
    assert {a["profile_version"] for a in parent["apps"]} == {"1.0.0;api35-arm64-v8a-AP3A.240905.015"}
    assert "parent-avd.microphone_verified" in manual and "child-avd.capture_allowed" in manual
    # 사람이 확인하지 않은 상태로는 준비 완료가 될 수 없다
    result = prepare(manifest)
    assert (result.status, result.diagnostics.source) == ("error", Source.LIVE)
    text = json.dumps(manifest)
    assert "emulator-5554" not in text and "@" not in text  # 기기 번호·계정 없음


def test_24_after_manual_confirmation_all_scenarios_ready():
    manifest, _, _ = collect()
    result = prepare(confirm_manually(manifest))
    assert (result.status, result.outcome) == ("success", "fixtures_ready")
    assert set(result.data.ready_scenarios) == set(_SCENARIOS)


def test_24_missing_gmail_blocks_only_mail_open():
    no_gmail = {pkg: v for pkg, v in ALL_APPS.items() if pkg != PACKAGES["gmail"]}
    manifest, _, _ = collect_manifest(Adb("p", fake_device(packages=no_gmail)), Adb("c", fake_device()))
    result = prepare(confirm_manually(manifest))
    assert (result.status, result.outcome) == ("partial", "some_fixtures_blocked")
    assert {b.scenario_id for b in result.data.blocked_scenarios} == {"MAIL_OPEN"}


def test_24_offline_avd_blocks_the_whole_rehearsal_and_warns():
    # 제어 채널도 네트워크가 필요해 점검기는 기기 단위로 막는다 (지도·Gmail만이 아니라 전체)
    manifest, _, warnings = collect_manifest(Adb("p", fake_device(validated=False)), Adb("c", fake_device()))
    assert any("network not validated" in w for w in warnings)
    result = prepare(confirm_manually(manifest))
    assert (result.status, result.data) == ("error", None)


def test_environment_warnings_for_locale_timezone_api_and_image():
    _, _, warnings = collect(locale="en-US", tz="UTC", sdk="34", build="OTHER.1")
    joined = "\n".join(warnings)
    for needle in ("locale en-US", "time_zone UTC", "android_api 34", "android_image differ"):
        assert needle in joined


def test_baseline_save_and_restore_settings():
    device = fake_device()
    adb = Adb("emulator-5554", device)
    baseline = save_baseline(adb)
    assert baseline == {"settings": {"system/font_scale": "1.0", "system/screen_off_timeout": "30000"},
                        "density_override": None}
    restored = restore_baseline(adb, baseline | {"settings": {**baseline["settings"], "system/font_scale": "null"}})
    assert restored == ["system/font_scale", "system/screen_off_timeout", "wm/density"]
    assert "settings delete system font_scale" in device.calls
    assert "settings put system screen_off_timeout 30000" in device.calls
    assert "wm density reset" in device.calls


def test_avd_plan_matches_host_architecture(capsys):
    arm = avd_plan("arm64-v8a")
    x86 = avd_plan("x86_64")
    assert any("system-images;android-35;google_apis_playstore;arm64-v8a" in line for line in arm)
    assert any("-port 5556 -change-locale ko-KR -timezone Asia/Seoul" in line for line in arm)
    assert not any("ARM64(Mac)" in line for line in arm)
    assert any("ARM64(Mac)" in line for line in x86)
    assert host_abi("AMD64") == "x86_64" and host_abi("arm64") == "arm64-v8a"
    assert main(["avd-plan", "--abi", "x86_64"]) == 0
    assert "hyojason-parent" in capsys.readouterr().out


def test_mac_setup_script_matches_avd_plan_and_parses():
    """팀 Mac 스크립트(결정 메모 11)가 avd-plan(arm64)과 같은 이미지·포트·언어·시간대를 쓰는지."""
    import pathlib
    import shutil
    import subprocess

    script = pathlib.Path(__file__).resolve().parents[3] / "demo" / "fixtures" / "setup_avd_mac.sh"
    text = script.read_text(encoding="utf-8")
    plan = "\n".join(avd_plan("arm64-v8a"))
    assert 'IMAGE="system-images;android-${ANDROID_API};google_apis_playstore;arm64-v8a"' in text
    assert "system-images;android-35;google_apis_playstore;arm64-v8a" in plan and "ANDROID_API=35" in text
    for needle in ("hyojason-parent", "hyojason-child", "-d pixel_7"):
        assert needle in text and needle in plan
    assert 'start_avd hyojason-parent 5554' in text and 'start_avd hyojason-child 5556' in text
    assert 'LOCALE="ko-KR"' in text and 'TIME_ZONE="Asia/Seoul"' in text
    assert "demo_env collect --parent emulator-5554 --child emulator-5556" in text
    bash = shutil.which("bash")
    if bash:
        assert subprocess.run([bash, "-n", str(script)], capture_output=True).returncode == 0
