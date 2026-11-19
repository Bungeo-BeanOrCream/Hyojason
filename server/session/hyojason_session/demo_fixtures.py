"""N-24 · Validate an externally checked rehearsal environment manifest."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date
from enum import StrEnum

from .common.enums import Role, Source
from .common.result import Basis, ErrorCode, NodeResult, NodeRun, ResultRef, StepStatus, new_id


class AudioRoute(StrEnum):
    SPEAKER = "SPEAKER"
    WIRED = "WIRED"
    BLUETOOTH = "BLUETOOTH"


@dataclass(frozen=True)
class AppProfile:
    app_id: str
    version: str
    profile_version: str
    start_screens: tuple[str, ...]
    observation_allowed: bool
    login_ready: bool
    network_ready: bool


@dataclass(frozen=True)
class DeviceProfile:
    device_id: str
    role: Role
    source: Source
    android_api: int
    android_image: str
    locale: str
    time_zone: str | None
    local_date: str | None
    online: bool
    microphone_verified: bool
    duplex_audio_verified: bool
    audio_route: AudioRoute
    host_control_reachable: bool
    avd_control_reachable: bool
    capture_allowed: bool
    overlay_allowed: bool
    apps: tuple[AppProfile, ...]


@dataclass(frozen=True)
class FixtureRef:
    """An approved logical alias for an asset checked by the integration owner."""

    scenario_id: str
    kind: str
    asset_id: str
    source: Source
    ready: bool


@dataclass(frozen=True)
class DemoManifest:
    device_profiles: tuple[DeviceProfile, ...]
    fixture_revision: int
    scenario_ids: tuple[str, ...]
    staged_assets: tuple[FixtureRef, ...]


class BlockCode(StrEnum):
    APP_MISSING = "APP_MISSING"
    START_SCREEN_MISSING = "START_SCREEN_MISSING"
    OBSERVATION_BLOCKED = "OBSERVATION_BLOCKED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    LOGIN_UNAVAILABLE = "LOGIN_UNAVAILABLE"
    CLOCK_CONTEXT_MISSING = "CLOCK_CONTEXT_MISSING"
    ASSET_NOT_READY = "ASSET_NOT_READY"


@dataclass(frozen=True)
class BlockReason:
    scenario_id: str
    code: BlockCode
    device_role: Role
    app_id: str
    asset_kind: str | None = None


@dataclass(frozen=True)
class DemoFixtureList:
    fixture_revision: int
    ready_scenarios: tuple[str, ...]
    blocked_scenarios: tuple[BlockReason, ...]


@dataclass(frozen=True)
class _Scenario:
    app_id: str
    start_screens: tuple[str, ...]
    assets: tuple[str, ...]


_SCENARIOS = {
    "A": _Scenario("settings", ("settings_home", "settings_display"),
                   ("settings_baseline", "fast_correct", "wrong_turn", "reverse_edge",
                    "below_fold", "visible_target")),
    "B": _Scenario("clock", ("home", "clock_alarm_list", "clock_alarm_edit"),
                   ("opening_instruction", "alarm_target", "post_call_path", "clock_reference")),
    "SET_FONT": _Scenario("settings", ("settings_home", "settings_display", "settings_display_size_text"),
                          ("settings_baseline",)),
    "SET_TIMEOUT": _Scenario("settings", ("settings_home", "settings_display", "settings_screen_timeout"),
                             ("settings_baseline",)),
    "CLOCK_ALARM": _Scenario("clock", ("clock_alarm_list", "clock_alarm_edit"), ("clock_reference",)),
    "MAP_ROUTE": _Scenario("maps", ("maps_home", "maps_search", "maps_place_detail", "maps_directions"),
                           ("public_place",)),
    "SMS_SEND": _Scenario("sms", ("sms_list", "sms_compose", "sms_conversation"),
                         ("sms_recipient", "sms_message", "sms_receiver")),
    "MAIL_OPEN": _Scenario("gmail", ("gmail_inbox", "gmail_mail_detail"), ("mail_sample",)),
}
_APPS = frozenset(spec.app_id for spec in _SCENARIOS.values()) | {"hyojason"}
_NETWORK_APPS = {"maps", "gmail"}
_ALIAS = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+;-]{0,95}\Z")
_SCREEN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_TIME_ZONE = re.compile(r"(?:UTC|[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)+)\Z")


def _mapping(raw: object, model: type, name: str) -> dict:
    if not isinstance(raw, Mapping) or raw.keys() != {field.name for field in fields(model)}:
        raise ValueError(f"Invalid {name} fields")
    return dict(raw)


def _sequence(raw: object) -> bool:
    return isinstance(raw, (list, tuple))


def _items(raw: object, name: str) -> list | tuple:
    if not _sequence(raw):
        raise ValueError(f"Invalid {name} list")
    return raw


def load_demo_manifest(raw: Mapping) -> DemoManifest:
    """Decode the four N-24 input fields. Unknown fields and raw media are rejected."""
    values = _mapping(raw, DemoManifest, "manifest")
    profiles = []
    for raw_device in _items(values["device_profiles"], "device_profiles"):
        device = _mapping(raw_device, DeviceProfile, "device profile")
        apps = []
        for raw_app in _items(device["apps"], "apps"):
            app = _mapping(raw_app, AppProfile, "app profile")
            app["start_screens"] = tuple(_items(app["start_screens"], "start_screens"))
            apps.append(AppProfile(**app))
        try:
            device["role"] = Role(device["role"])
            device["source"] = Source(device["source"])
            device["audio_route"] = AudioRoute(device["audio_route"])
        except (TypeError, ValueError):
            raise ValueError("Invalid device classification") from None
        profiles.append(DeviceProfile(**(device | {"apps": tuple(apps)})))
    assets = []
    for raw_asset in _items(values["staged_assets"], "staged_assets"):
        asset = _mapping(raw_asset, FixtureRef, "asset reference")
        try:
            asset["source"] = Source(asset["source"])
        except (TypeError, ValueError):
            raise ValueError("Invalid asset provenance") from None
        assets.append(FixtureRef(**asset))
    return DemoManifest(
        tuple(profiles), values["fixture_revision"],
        tuple(_items(values["scenario_ids"], "scenario_ids")), tuple(assets),
    )


def _match(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _bools(*values: object) -> bool:
    return all(type(value) is bool for value in values)


def _iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_app(app: object) -> bool:
    return (
        isinstance(app, AppProfile) and isinstance(app.app_id, str) and app.app_id in _APPS
        and _match(_VERSION, app.version) and _match(_VERSION, app.profile_version)
        and _sequence(app.start_screens) and all(_match(_SCREEN, screen) for screen in app.start_screens)
        and len(set(app.start_screens)) == len(app.start_screens)
        and _bools(app.observation_allowed, app.login_ready, app.network_ready)
    )


def _valid_device(profile: object) -> bool:
    return (
        isinstance(profile, DeviceProfile) and _match(_ALIAS, profile.device_id)
        and isinstance(profile.role, Role) and isinstance(profile.source, Source)
        and type(profile.android_api) is int and profile.android_api > 0
        and _match(_VERSION, profile.android_image) and _match(_ALIAS, profile.locale)
        and (profile.time_zone is None or _match(_TIME_ZONE, profile.time_zone))
        and (profile.local_date is None or _iso_date(profile.local_date))
        and isinstance(profile.audio_route, AudioRoute)
        and _bools(profile.online, profile.microphone_verified, profile.duplex_audio_verified,
                   profile.host_control_reachable, profile.avd_control_reachable,
                   profile.capture_allowed, profile.overlay_allowed)
        and _sequence(profile.apps) and all(_valid_app(app) for app in profile.apps)
        and len({app.app_id for app in profile.apps}) == len(profile.apps)
    )


def _valid_asset(asset: object) -> bool:
    return (
        isinstance(asset, FixtureRef) and isinstance(asset.scenario_id, str)
        and asset.scenario_id in _SCENARIOS and asset.kind in _SCENARIOS[asset.scenario_id].assets
        and _match(_ALIAS, asset.asset_id) and isinstance(asset.source, Source)
        and type(asset.ready) is bool
    )


def _valid_manifest(devices, revision, scenarios, assets) -> bool:
    return (
        type(revision) is int and revision >= 0 and _sequence(devices) and len(devices) <= 2
        and all(_valid_device(device) for device in devices)
        and len({device.role for device in devices}) == len(devices)
        and len({device.device_id for device in devices}) == len(devices)
        and _sequence(scenarios) and bool(scenarios)
        and all(isinstance(item, str) and item in _SCENARIOS for item in scenarios)
        and len(set(scenarios)) == len(scenarios)
        and _sequence(assets) and all(_valid_asset(asset) for asset in assets)
        and len({(asset.scenario_id, asset.kind) for asset in assets}) == len(assets)
    )


def _device_blockers(devices: dict[Role, DeviceProfile]) -> dict[str, str]:
    problems = {}
    for role in Role:
        device = devices.get(role)
        step = role.value.lower()
        if device is None:
            problems[step] = "ROLE_MISSING"
            continue
        checks = (
            ("ONLINE", device.online),
            ("LOCALE", device.locale == "ko-KR"),
            ("HOST_CONTROL", device.host_control_reachable),
            ("AVD_CONTROL", device.avd_control_reachable),
            ("MICROPHONE", device.microphone_verified),
            ("DUPLEX_AUDIO", device.duplex_audio_verified),
        )
        for check, verified in checks:
            if not verified:
                problems[f"{step}_{check.lower()}"] = f"{check}_NOT_READY"
        if role is Role.PARENT and not (device.capture_allowed and device.overlay_allowed):
            problems["parent_permissions"] = "CAPTURE_OR_OVERLAY_UNAVAILABLE"
        app = next((app for app in device.apps if app.app_id == "hyojason"), None)
        if app is None or not app.login_ready:
            problems[f"{step}_app"] = "ROLE_APP_NOT_READY"
    if not problems:
        parent, child = devices[Role.PARENT], devices[Role.CHILD]
        if (parent.android_api, parent.android_image) != (child.android_api, child.android_image):
            problems["android_pair"] = "ANDROID_PROFILE_MISMATCH"
        parent_app = next(app for app in parent.apps if app.app_id == "hyojason")
        child_app = next(app for app in child.apps if app.app_id == "hyojason")
        if (parent_app.version, parent_app.profile_version) != (child_app.version, child_app.profile_version):
            problems["apk_pair"] = "ROLE_APP_PROFILE_MISMATCH"
    return problems


def _scenario_blockers(scenario_id, parent, parent_apps, child_apps, assets) -> tuple[BlockReason, ...]:
    spec = _SCENARIOS[scenario_id]
    app = parent_apps.get(spec.app_id)
    blockers = []
    if app is None:
        blockers.append(BlockCode.APP_MISSING)
    else:
        if not set(spec.start_screens).issubset(app.start_screens):
            blockers.append(BlockCode.START_SCREEN_MISSING)
        if not app.observation_allowed:
            blockers.append(BlockCode.OBSERVATION_BLOCKED)
        if spec.app_id in _NETWORK_APPS:
            if not app.network_ready:
                blockers.append(BlockCode.NETWORK_UNAVAILABLE)
            if not app.login_ready:
                blockers.append(BlockCode.LOGIN_UNAVAILABLE)
    if spec.app_id == "clock" and not (parent.time_zone and parent.local_date):
        blockers.append(BlockCode.CLOCK_CONTEXT_MISSING)
    result = [BlockReason(scenario_id, code, Role.PARENT, spec.app_id) for code in blockers]
    if spec.app_id == "sms" and "sms" not in child_apps:
        result.append(BlockReason(scenario_id, BlockCode.APP_MISSING, Role.CHILD, spec.app_id))
    for kind in spec.assets:
        ref = assets.get((scenario_id, kind))
        if ref is None or not ref.ready:
            role = Role.CHILD if kind == "sms_receiver" else Role.PARENT
            result.append(BlockReason(scenario_id, BlockCode.ASSET_NOT_READY, role, spec.app_id, kind))
    return tuple(result)


def prepare_demo_fixtures(
    device_profiles: Sequence[DeviceProfile],
    fixture_revision: int,
    scenario_ids: Sequence[str],
    staged_assets: Sequence[FixtureRef],
    *,
    basis: Basis = Basis(),
    trace_id: str | None = None,
    source: Source = Source.LIVE,
    input_refs: Sequence[ResultRef] = (),
) -> NodeResult[DemoFixtureList]:
    """Classify separately ready scenarios without launching or changing the devices."""
    if any(isinstance(item, (DeviceProfile, FixtureRef)) and item.source is Source.FIXTURE
           for collection in (device_profiles, staged_assets) if _sequence(collection)
           for item in collection):
        source = Source.FIXTURE
    run = NodeRun("N-24", trace_id or new_id("trace"), source)
    if not _valid_manifest(device_profiles, fixture_revision, scenario_ids, staged_assets):
        return run.error(ErrorCode.INVALID_INPUT, "validate", basis, input_refs=input_refs)
    run.step("validate", StepStatus.SUCCESS)
    devices = {device.role: device for device in device_profiles}
    blockers = _device_blockers(devices)
    if blockers:
        for step, code in blockers.items():
            run.step(step, StepStatus.FAILED, code)
        return run.error(ErrorCode.DEPENDENCY_UNAVAILABLE, "device_readiness", basis, input_refs=input_refs)
    run.step("device_readiness", StepStatus.SUCCESS)

    parent = devices[Role.PARENT]
    parent_apps = {app.app_id: app for app in parent.apps}
    child_apps = {app.app_id: app for app in devices[Role.CHILD].apps}
    assets = {(asset.scenario_id, asset.kind): asset for asset in staged_assets}
    ready = []
    blocked = []
    for scenario_id in scenario_ids:
        reasons = _scenario_blockers(scenario_id, parent, parent_apps, child_apps, assets)
        if reasons:
            blocked.extend(reasons)
            run.step(f"scenario_{scenario_id}", StepStatus.FAILED, reasons[0].code.value, count=len(reasons))
        else:
            ready.append(scenario_id)
            run.step(f"scenario_{scenario_id}", StepStatus.SUCCESS)
    if not ready:
        return run.error(ErrorCode.DEPENDENCY_UNAVAILABLE, "scenario_readiness", basis, input_refs=input_refs)
    data = DemoFixtureList(fixture_revision, tuple(ready), tuple(blocked))
    if blocked:
        return run.partial("some_fixtures_blocked", data, basis, input_refs)
    return run.success("fixtures_ready", data, basis, input_refs)
