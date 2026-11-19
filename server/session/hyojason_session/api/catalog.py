"""제어 서버 기본 카탈로그.

값의 출처
- 화면·대상 ID·상태 근거: docs/screen-observation-profile.md (이슈 #3 A·D 합의)
- 목표별 슬롯: demo/fixtures/goals.json (시연 자산 ID만, 이름·번호·주소 같은 실제 값 없음)

AVD 확인 전 초기값
- REVERSIBLE_PAIRS: 같은 앱 안에서 '뒤로 가기'로 돌아올 수 있다고 보는 화면 쌍.
- 근거 코드는 합의된 상태 근거 7개와 B-05 결과 근거(B05_RESULT_EVIDENCE)다. A의 N-09 탐지 근거 코드
  (C가 제안한 scroll_down:/below_fold:/activated:/alarm_next:<날짜>:<시각> 등)가 A·C·D 합의로 정해지면
  default_catalog(extra_evidence=...)로 더한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping

from ..navigation import NavigationCatalog

SCREEN_APPS: Mapping[str, str] = {
    "home": "launcher",
    "settings_home": "settings",
    "settings_display": "settings",
    "settings_display_size_text": "settings",
    "settings_screen_timeout": "settings",
    "clock_other_tab": "clock",
    "clock_alarm_list": "clock",
    "clock_alarm_edit": "clock",
    "maps_home": "maps",
    "maps_search": "maps",
    "maps_place_detail": "maps",
    "maps_directions": "maps",
    "sms_list": "sms",
    "sms_compose": "sms",
    "sms_conversation": "sms",
    "gmail_nav_drawer": "gmail",
    "gmail_inbox": "gmail",
    "gmail_mail_detail": "gmail",
}

TARGET_IDS: tuple[str, ...] = (
    "launcher.settings", "launcher.clock", "launcher.maps", "launcher.sms", "launcher.gmail",
    "settings.display", "settings.display_size_text", "settings.screen_timeout",
    "settings.font_size_larger", "settings.timeout.10m",
    "clock.alarm_tab", "clock.add_alarm", "clock.save_alarm",
    "maps.search_box", "maps.demo_place_result", "maps.directions",
    "sms.compose", "sms.demo_recipient", "sms.text_field", "sms.send",
    "gmail.inbox", "gmail.demo_mail",
)

STATE_EVIDENCE: tuple[str, ...] = (
    "font_scale_changed", "timeout_10m_selected", "alarm_card_active", "alarm_next_tomorrow",
    "route_shown", "sms_sent_status", "mail_detail_open",
)

# C(B-05)가 target.located·action.assessed 결과에 싣는 근거 코드. 화면 관찰 근거가 아니라 서버 판단 결과의
# 출처 표시다 (server/agent/README.md '화면과 근거': "D 카탈로그가 B-05 결과 코드를 허용해야 한다").
# enumerate_action_candidate: visible_target, verified_reverse_edge / assess_instruction_follow_through: observed:<대상>
B05_RESULT_EVIDENCE: tuple[str, ...] = (
    "visible_target", "verified_reverse_edge", *(f"observed:{target}" for target in TARGET_IDS),
)

REVERSIBLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("settings_home", "settings_display"),
    ("settings_display", "settings_display_size_text"),
    ("settings_display", "settings_screen_timeout"),
    ("clock_alarm_list", "clock_alarm_edit"),
    ("maps_home", "maps_search"),
    ("maps_search", "maps_place_detail"),
    ("maps_place_detail", "maps_directions"),
    ("sms_list", "sms_compose"),
    ("gmail_nav_drawer", "gmail_inbox"),
    ("gmail_inbox", "gmail_mail_detail"),
)


def default_catalog(extra_evidence: Iterable[str] = ()) -> NavigationCatalog:
    return NavigationCatalog(
        screen_apps=SCREEN_APPS,
        target_ids=TARGET_IDS,
        evidence_codes=tuple(STATE_EVIDENCE) + B05_RESULT_EVIDENCE + tuple(extra_evidence),
        reversible_pairs=REVERSIBLE_PAIRS,
    )


_ASSET = re.compile(r"demo-[a-z0-9-]{1,60}\Z")
_TIME = re.compile(r"([01]\d|2[0-3]):[0-5]\d\Z")


def _asset(value: object) -> bool:
    return isinstance(value, str) and _ASSET.fullmatch(value) is not None


def _set_font(s: Mapping) -> bool:
    return set(s) <= {"steps"} and ("steps" not in s or (type(s["steps"]) is int and 1 <= s["steps"] <= 3))


def _set_timeout(s: Mapping) -> bool:
    return set(s) <= {"duration_minutes"} and (
        "duration_minutes" not in s or (type(s["duration_minutes"]) is int and 1 <= s["duration_minutes"] <= 30)
    )


def _clock_alarm(s: Mapping) -> bool:
    return (
        "time" in s and set(s) <= {"day", "time"}
        and isinstance(s["time"], str) and _TIME.fullmatch(s["time"]) is not None
        and s.get("day", "today") in ("today", "tomorrow")
    )


APPROVED_SLOTS: Mapping[str, Callable[[Mapping], bool]] = {
    "SET_FONT": _set_font,
    "SET_TIMEOUT": _set_timeout,
    "CLOCK_ALARM": _clock_alarm,
    "MAP_ROUTE": lambda s: set(s) == {"place"} and _asset(s["place"]),
    "SMS_SEND": lambda s: set(s) == {"recipient_id", "message_id"} and all(_asset(v) for v in s.values()),
    "MAIL_OPEN": lambda s: set(s) == {"mail_id"} and _asset(s["mail_id"]),
}
