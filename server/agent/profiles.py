"""이슈 #3의 시연 화면 프로필과 판단에 필요한 관측 근거."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

from ..contracts import Action, Goal, GoalId, JsonValue, ScreenState


@dataclass(frozen=True)
class TargetProfile:
    screen: str
    label: str
    aliases: tuple[str, ...] = ()
    destination: str | None = None
    action: Action = "TAP"


TARGETS: Final[dict[str, TargetProfile]] = {
    "launcher.settings": TargetProfile("home", "설정 앱", ("설정",), "settings_home"),
    "launcher.clock": TargetProfile("home", "시계 앱", ("시계",), "clock_alarm_list"),
    "launcher.maps": TargetProfile(
        "home", "지도 앱", ("구글 지도", "지도"), "maps_home"
    ),
    "launcher.sms": TargetProfile("home", "메시지 앱", ("문자 앱",), "sms_list"),
    "launcher.gmail": TargetProfile(
        "home", "Gmail 앱", ("지메일", "메일 앱"), "gmail_inbox"
    ),
    "settings.display": TargetProfile(
        "settings_home", "디스플레이", ("화면 설정",), "settings_display"
    ),
    "settings.display_size_text": TargetProfile(
        "settings_display",
        "표시 크기 및 텍스트",
        ("글자 크기", "폰트 크기"),
        "settings_display_size_text",
    ),
    "settings.screen_timeout": TargetProfile(
        "settings_display",
        "화면 자동 꺼짐",
        ("자동 꺼짐", "화면 꺼짐"),
        "settings_screen_timeout",
    ),
    "settings.timeout.10m": TargetProfile(
        "settings_screen_timeout", "10분", ("십 분",)
    ),
    "settings.font_size_larger": TargetProfile(
        "settings_display_size_text", "글자 크게", ("큰 쪽",)
    ),
    "clock.alarm_tab": TargetProfile(
        "clock_other_tab", "알람 탭", (), "clock_alarm_list"
    ),
    "clock.add_alarm": TargetProfile(
        "clock_alarm_list", "알람 추가", ("플러스", "추가 버튼"), "clock_alarm_edit"
    ),
    "clock.time_picker": TargetProfile(
        "clock_alarm_edit", "알람 시각", action="INPUT_HINT"
    ),
    "clock.save_alarm": TargetProfile(
        "clock_alarm_edit", "저장 버튼", ("저장",), "clock_alarm_list"
    ),
    "maps.search_box": TargetProfile("maps_home", "검색창", (), "maps_search"),
    "maps.destination_field": TargetProfile(
        "maps_search", "목적지 검색 입력", action="INPUT_HINT"
    ),
    "maps.demo_place_result": TargetProfile(
        "maps_search", "일치하는 시연 장소", (), "maps_place_detail"
    ),
    "maps.directions": TargetProfile(
        "maps_place_detail", "길찾기", (), "maps_directions"
    ),
    "sms.compose": TargetProfile("sms_list", "새 문자", ("새 대화",), "sms_compose"),
    "sms.demo_recipient": TargetProfile(
        "sms_compose", "승인된 시연 상대", (), "sms_conversation"
    ),
    "sms.text_field": TargetProfile(
        "sms_conversation", "문자 입력창", action="INPUT_HINT"
    ),
    "sms.send": TargetProfile("sms_conversation", "보내기 버튼", ("보내기",)),
    "gmail.inbox": TargetProfile("gmail_nav_drawer", "받은편지함", (), "gmail_inbox"),
    "gmail.demo_mail": TargetProfile(
        "gmail_inbox", "시연 메일", ("테스트 메일",), "gmail_mail_detail"
    ),
}
GOAL_LABELS: Final[dict[GoalId, str]] = {
    "SET_FONT": "설정에서 글자 크기를 키우기",
    "SET_TIMEOUT": "설정에서 화면 자동 꺼짐 시간을 10분으로 바꾸기",
    "CLOCK_ALARM": "시계에서 지정된 시각의 알람을 맞추기",
    "MAP_ROUTE": "지도에서 지정 시연 장소의 길찾기 화면 열기",
    "SMS_SEND": "승인된 시연 상대에게 시연 메시지 보내기",
    "MAIL_OPEN": "지정된 시연 메일 열기",
}
GOAL_ROUTES: Final[dict[GoalId, tuple[str, ...]]] = {
    "SET_FONT": (
        "launcher.settings",
        "settings.display",
        "settings.display_size_text",
        "settings.font_size_larger",
    ),
    "SET_TIMEOUT": (
        "launcher.settings",
        "settings.display",
        "settings.screen_timeout",
        "settings.timeout.10m",
    ),
    "CLOCK_ALARM": (
        "launcher.clock",
        "clock.alarm_tab",
        "clock.add_alarm",
        "clock.time_picker",
        "clock.save_alarm",
    ),
    "MAP_ROUTE": (
        "launcher.maps",
        "maps.search_box",
        "maps.destination_field",
        "maps.demo_place_result",
        "maps.directions",
    ),
    "SMS_SEND": (
        "launcher.sms",
        "sms.compose",
        "sms.demo_recipient",
        "sms.text_field",
        "sms.send",
    ),
    "MAIL_OPEN": ("launcher.gmail", "gmail.inbox", "gmail.demo_mail"),
}
POLICY_REVISION: Final = 1
_DANGEROUS = (
    "공장초기화",
    "계정삭제",
    "계정제거",
    "문자삭제",
    "메일삭제",
    "비밀번호",
    "인증번호",
    "송금",
)


def normalize(text: str) -> str:
    return re.sub(r"[^\w]", "", text.casefold()).replace("_", "")


def is_dangerous(text: str) -> bool:
    return any(word in normalize(text) for word in _DANGEROUS)


def is_protected(screen: ScreenState) -> bool:
    return (
        screen.screen_kind == "protected"
        or screen.observation_health.upper() == "PROTECTED"
    )


def is_stable(screen: ScreenState) -> bool:
    # ACTIVE는 세션 서버, stable은 저장소의 초기 합성 입력에서 쓰는 표기다.
    return (
        screen.screen_kind != "unknown"
        and not is_protected(screen)
        and screen.observation_health.upper() in {"ACTIVE", "STABLE"}
    )


def target_profile(target_id: str, screen_kind: str) -> TargetProfile | None:
    profile = TARGETS.get(target_id)
    return profile if profile and profile.screen == screen_kind else None


def below_fold_evidence(screen: ScreenState, target_id: str) -> tuple[str, ...]:
    if target_id in screen.target_ids:
        return ()
    for code in screen.evidence_codes:
        parts = code.split(":")
        if len(parts) == 3 and parts[0] == "below_fold" and parts[2] == target_id:
            container = parts[1]
            match = f"scroll_down:{container}"
            if container and match in screen.evidence_codes:
                return (match, code)
    return ()


def required_arrival_evidence(goal: Goal) -> tuple[str, ...]:
    slots = goal.approved_slots
    match goal.goal_id:
        case "SET_FONT":
            return ("font_scale_changed",) if slots.get("steps", 1) in (1, "1") else ()
        case "SET_TIMEOUT":
            return (
                ("timeout_10m_selected",)
                if slots.get("duration_minutes") in (10, "10")
                else ()
            )
        case "CLOCK_ALARM":
            token = alarm_token(slots)
            if token:
                return ("alarm_card_active", f"alarm_next:{token}")
            return ()
        # 슬롯은 D의 시연 자산 형식(demo/fixtures/goals.json). 도착 근거는 #3 제안대로
        # 상태 코드와 같은 화면의 값 코드(D 자산 ID)를 함께 요구해 다른 목적지·수신자의 조기 도착을 막는다.
        # 값 코드는 A가 닫힌 집합으로 보내기로 합의되면 D 카탈로그에 등록된다.
        case "MAP_ROUTE":
            place = slots.get("place")
            return ("route_shown", f"route_destination:{place}") if _is_asset(place) else ()
        case "SMS_SEND":
            recipient, message = slots.get("recipient_id"), slots.get("message_id")
            if _is_asset(recipient) and _is_asset(message):
                return ("sms_sent_status", f"sms_sent:{recipient}:{message}")
            return ()
        case "MAIL_OPEN":
            return ("mail_detail_open",) if _is_asset(slots.get("mail_id")) else ()
    return ()


def sms_draft_evidence(recipient_id: object = "demo-recipient", message_id: object = "demo-sms") -> str:
    """문자 초안이 승인된 상대·문구로 채워졌다는 근거 코드. 값은 D 시연 자산 ID (#3)."""
    return f"sms_draft:{recipient_id}:{message_id}"


def _is_asset(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"demo-[a-z0-9-]{1,60}", value) is not None


def alarm_token(slots: dict[str, JsonValue]) -> str | None:
    day, time = slots.get("day"), slots.get("time")
    if day not in ("today", "tomorrow") or not isinstance(time, str):
        return None
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
        return None
    return f"{day}:{time}"


_ARRIVAL_SCREENS: Final[dict[GoalId, set[str]]] = {
    "SET_FONT": {"settings_display_size_text"},
    "SET_TIMEOUT": {"settings_screen_timeout", "settings_display"},
    "CLOCK_ALARM": {"clock_alarm_list"},
    "MAP_ROUTE": {"maps_directions"},
    "SMS_SEND": {"sms_conversation"},
    "MAIL_OPEN": {"gmail_mail_detail"},
}


def arrival_evidence(goal: Goal, screen: ScreenState) -> tuple[str, ...]:
    required = required_arrival_evidence(goal)
    if (
        required
        and is_stable(screen)
        and screen.screen_kind in _ARRIVAL_SCREENS.get(goal.goal_id, ())
        and set(required).issubset(screen.evidence_codes)
    ):
        return required
    return ()
