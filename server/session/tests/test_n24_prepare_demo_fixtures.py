from dataclasses import replace

import pytest

from hyojason_session.common.enums import Role, Source
from hyojason_session.common.result import Basis, ErrorCode, Status
from hyojason_session.common.serialize import to_jsonable
from hyojason_session.demo_fixtures import (
    AppProfile,
    AudioRoute,
    BlockCode,
    DemoManifest,
    DeviceProfile,
    FixtureRef,
    load_demo_manifest,
    prepare_demo_fixtures,
)


def app(app_id, *screens):
    return AppProfile(app_id, "1.0", "avd35-test", screens, True, True, True)


def device(role, *apps):
    return DeviceProfile(
        device_id=f"demo-{role.lower()}",
        role=role,
        source=Source.LIVE,
        android_api=35,
        android_image="system-images;android-35;google_apis;arm64-v8a",
        locale="ko-KR",
        time_zone="Asia/Seoul",
        local_date="2026-11-19",
        online=True,
        microphone_verified=True,
        duplex_audio_verified=True,
        audio_route=AudioRoute.WIRED if role is Role.CHILD else AudioRoute.SPEAKER,
        host_control_reachable=True,
        avd_control_reachable=True,
        capture_allowed=role is Role.PARENT,
        overlay_allowed=role is Role.PARENT,
        apps=(app("hyojason"), *apps),
    )


@pytest.fixture
def manifest():
    parent = device(
        Role.PARENT,
        app("settings", "settings_home", "settings_display",
            "settings_display_size_text", "settings_screen_timeout"),
        app("clock", "home", "clock_alarm_list", "clock_alarm_edit"),
        app("maps", "maps_home", "maps_search", "maps_place_detail", "maps_directions"),
        app("sms", "sms_list", "sms_compose", "sms_conversation"),
        app("gmail", "gmail_inbox", "gmail_mail_detail"),
    )
    child = device(Role.CHILD, app("sms"))
    requirements = {
        "A": ("settings_baseline", "fast_correct", "wrong_turn", "reverse_edge",
              "below_fold", "visible_target"),
        "B": ("opening_instruction", "alarm_target", "post_call_path", "clock_reference"),
        "SET_FONT": ("settings_baseline",),
        "SET_TIMEOUT": ("settings_baseline",),
        "CLOCK_ALARM": ("clock_reference",),
        "MAP_ROUTE": ("public_place",),
        "SMS_SEND": ("sms_recipient", "sms_message", "sms_receiver"),
        "MAIL_OPEN": ("mail_sample",),
    }
    assets = tuple(
        FixtureRef(scenario, kind, f"demo-{scenario.lower()}-{kind}", Source.LIVE, True)
        for scenario, kinds in requirements.items() for kind in kinds
    )
    return DemoManifest((parent, child), 7, tuple(requirements), assets)


def evaluate(manifest, **options):
    return prepare_demo_fixtures(
        manifest.device_profiles, manifest.fixture_revision, manifest.scenario_ids,
        manifest.staged_assets, **options,
    )


def test_24_1_full_manifest_round_trip_with_all_goals_and_rehearsals(manifest):
    template = load_demo_manifest(to_jsonable(manifest))
    assert template == manifest
    result = evaluate(template, trace_id="demo-fixture-ready")
    assert result.status is Status.SUCCESS
    assert result.outcome == "fixtures_ready"
    assert result.data.fixture_revision == 7
    assert result.data.ready_scenarios == manifest.scenario_ids
    assert result.data.blocked_scenarios == ()
    assert result.diagnostics.node_id == "N-24"
    assert result.diagnostics.source is Source.LIVE


def test_24_2_one_unverified_asset_blocks_only_its_rehearsal(manifest):
    assets = tuple(
        replace(asset, ready=False, source=Source.FIXTURE)
        if (asset.scenario_id, asset.kind) == ("A", "reverse_edge") else asset
        for asset in manifest.staged_assets
    )
    result = evaluate(replace(manifest, staged_assets=assets))
    assert result.status is Status.PARTIAL
    assert result.outcome == "some_fixtures_blocked"
    assert result.diagnostics.source is Source.FIXTURE
    assert result.diagnostics.terminal_error is None
    assert result.data.ready_scenarios == manifest.scenario_ids[1:]
    reason, = result.data.blocked_scenarios
    assert (reason.scenario_id, reason.code, reason.asset_kind) == (
        "A", BlockCode.ASSET_NOT_READY, "reverse_edge",
    )


@pytest.mark.parametrize(
    ("profile_change", "step", "code"),
    [
        ({"online": False}, "child_online", "ONLINE_NOT_READY"),
        ({"locale": "en-US"}, "child_locale", "LOCALE_NOT_READY"),
        ({"avd_control_reachable": False}, "child_avd_control", "AVD_CONTROL_NOT_READY"),
        ({"host_control_reachable": False}, "child_host_control", "HOST_CONTROL_NOT_READY"),
        ({"microphone_verified": False}, "child_microphone", "MICROPHONE_NOT_READY"),
        ({"duplex_audio_verified": False}, "child_duplex_audio", "DUPLEX_AUDIO_NOT_READY"),
        ({"android_api": 34}, "android_pair", "ANDROID_PROFILE_MISMATCH"),
    ],
)
def test_24_3_unready_required_device_blocks_all_runs(manifest, profile_change, step, code):
    parent, child = manifest.device_profiles
    result = evaluate(replace(manifest, device_profiles=(parent, replace(child, **profile_change))))
    assert result.status is Status.ERROR and result.data is None and result.outcome is None
    assert result.diagnostics.terminal_error.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    assert result.diagnostics.steps[step].code == code


def test_24_3_missing_child_or_parent_capture_permission(manifest):
    parent, child = manifest.device_profiles
    missing_child = evaluate(replace(manifest, device_profiles=(parent,)))
    assert missing_child.diagnostics.steps["child"].code == "ROLE_MISSING"
    denied_capture = evaluate(replace(manifest, device_profiles=(replace(parent, capture_allowed=False), child)))
    assert denied_capture.status is Status.ERROR
    assert denied_capture.diagnostics.steps["parent_permissions"].code == "CAPTURE_OR_OVERLAY_UNAVAILABLE"


def test_24_4_gmail_and_maps_are_independent_and_keep_basis(manifest):
    parent, child = manifest.device_profiles
    apps = tuple(replace(app, network_ready=False) if app.app_id == "maps" else app for app in parent.apps)
    basis = Basis(pair_id="demo-pair", event_seq=18)
    mixed = replace(manifest, device_profiles=(replace(parent, apps=apps, source=Source.FIXTURE), child),
                    scenario_ids=("MAIL_OPEN", "MAP_ROUTE"))
    result = evaluate(mixed, basis=basis, trace_id="map-network-failure")
    assert result.status is Status.PARTIAL
    assert result.data.ready_scenarios == ("MAIL_OPEN",)
    reason, = result.data.blocked_scenarios
    assert (reason.scenario_id, reason.code) == ("MAP_ROUTE", BlockCode.NETWORK_UNAVAILABLE)
    assert result.diagnostics.basis is basis
    assert result.diagnostics.source is Source.FIXTURE
    assert result.diagnostics.trace_id == "map-network-failure"
    assert result.ref.run_id == result.diagnostics.run_id
    assert result.diagnostics.steps["scenario_MAP_ROUTE"].code == "NETWORK_UNAVAILABLE"


def test_clock_and_sms_preflight_are_scoped_to_their_dependencies(manifest):
    parent, child = manifest.device_profiles
    profiles = (replace(parent, local_date=None), replace(child, apps=(app("hyojason"),)))
    result = evaluate(replace(manifest, device_profiles=profiles))
    assert {"B", "CLOCK_ALARM", "SMS_SEND"} == {reason.scenario_id for reason in result.data.blocked_scenarios}
    sms = next(reason for reason in result.data.blocked_scenarios if reason.scenario_id == "SMS_SEND")
    assert sms.device_role is Role.CHILD and sms.code is BlockCode.APP_MISSING


def test_zero_ready_scenarios_returns_error_with_sanitized_diagnostic(manifest):
    result = evaluate(replace(manifest, scenario_ids=("MAIL_OPEN",), staged_assets=()))
    assert result.status is Status.ERROR
    assert result.data is None
    assert result.diagnostics.steps["scenario_MAIL_OPEN"].code == "ASSET_NOT_READY"


def test_fake_ids_and_unexpected_personal_data_are_rejected_without_echo(manifest):
    secret = "test-account@example.test"
    raw = to_jsonable(manifest)
    raw["staged_assets"][0]["account"] = secret
    with pytest.raises(ValueError, match="Invalid asset reference fields") as exc:
        load_demo_manifest(raw)
    assert secret not in str(exc.value)
    malformed = replace(manifest, staged_assets=(replace(manifest.staged_assets[0], asset_id=secret),))
    result = evaluate(malformed)
    assert result.status is Status.ERROR
    assert result.diagnostics.terminal_error.code == ErrorCode.INVALID_INPUT
    assert secret not in str(to_jsonable(result))
