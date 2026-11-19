package com.hyojason.app.observer.detection

internal const val UNKNOWN_SCREEN_KIND = "unknown"
internal const val PROTECTED_SCREEN_KIND = "protected"

/** Explicitly scoped to the synthetic fixtures until labels and layout are checked on the device. */
object DemoProfiles {
    val fixture = DetectionProfile(
        "demo-ko-fixture", 1, ProfileVersion("demo-fixture-1", "synthetic-api35"), listOf(
            profile("home", setOf("홈"), mapOf(
                "launcher.settings" to "설정", "launcher.clock" to "시계",
                "launcher.maps" to "지도", "launcher.sms" to "메시지", "launcher.gmail" to "Gmail",
            )),
            profile("settings_home", setOf("설정", "네트워크 및 인터넷"), mapOf("settings.display" to "디스플레이")),
            profile("settings_display", setOf("디스플레이", "밝기 수준"), mapOf(
                "settings.display_size_text" to "표시 크기 및 텍스트",
                "settings.screen_timeout" to "화면 자동 꺼짐",
            ), mapOf("timeout_10m_selected" to "timeout_10m_selected")),
            profile("settings_display_size_text", setOf("표시 크기 및 텍스트", "미리보기"), emptyMap(),
                mapOf("font_scale_changed" to "font_scale_changed")),
            profile("settings_screen_timeout", setOf("화면 자동 꺼짐", "1분"), mapOf("settings.timeout.10m" to "10분"),
                mapOf("timeout_10m_selected" to "timeout_10m_selected")),
            markerProfile("clock_other_tab", mapOf("clock.alarm_tab" to text("알람"))),
            ProfileScreen(
                "clock_alarm_list",
                listOf(setOf(text("알람"), text("스톱워치")), setOf(icon("clock_alarm_list"))),
                mapOf("clock.add_alarm" to TargetRule(setOf(icon("add_alarm")))) +
                    repeated("clock.alarm_toggle", text("알람 켜기"), listOf("alarm_fixture_row_a", "alarm_fixture_row_b")),
                mapOf("alarm_card_active" to state("active_alarm_card"), "alarm_next_tomorrow" to state("alarm_next_tomorrow")),
            ),
            profile("clock_alarm_edit", setOf("알람 편집"), mapOf("clock.save_alarm" to "저장")),
            profile("maps_home", setOf("Google 지도"), mapOf("maps.search_box" to "여기에서 검색")),
            markerProfile("maps_search", mapOf("maps.demo_place_result" to icon("demo_place_result"))),
            profile("maps_place_detail", setOf("개요", "리뷰"), mapOf("maps.directions" to "길찾기")),
            markerProfile("maps_directions", signals = mapOf("route_shown" to "route_shown")),
            markerProfile("sms_list", mapOf("sms.compose" to text("채팅 시작"))),
            markerProfile("sms_compose", mapOf("sms.demo_recipient" to icon("demo_recipient"))),
            ProfileScreen(
                "sms_conversation",
                listOf(setOf(icon("sms_conversation"))),
                mapOf(
                    "sms.text_field" to TargetRule(setOf(icon("message_field"))),
                    "sms.send" to TargetRule(
                        setOf(text("보내기")),
                        requiredFeatures = setOf(state("demo_recipient_verified"), state("demo_draft_verified")),
                    ),
                ),
                mapOf("sms_sent_status" to state("sms_sent_status")),
            ),
            markerProfile("gmail_nav_drawer", mapOf("gmail.inbox" to text("받은편지함"))),
            ProfileScreen("gmail_inbox", listOf(setOf(text("기본"), text("받은편지함"))),
                mapOf("gmail.demo_mail" to TargetRule(setOf(icon("demo_mail"))))),
            ProfileScreen("gmail_mail_detail", listOf(setOf(icon("mail_detail"))), emptyMap(),
                mapOf("mail_detail_open" to state("demo_mail_open"))),
        ),
    )

    private fun profile(
        kind: String,
        anchors: Set<String>,
        targets: Map<String, String>,
        signals: Map<String, String> = emptyMap(),
    ) = ProfileScreen(
        kind, listOf(anchors.mapTo(mutableSetOf(), ::text)),
        targets.mapValues { TargetRule(setOf(text(it.value))) },
        signals.mapValues { state(it.value) },
    )

    private fun markerProfile(
        kind: String,
        targets: Map<String, FeatureKey> = emptyMap(),
        signals: Map<String, String> = emptyMap(),
    ) = ProfileScreen(
        kind, listOf(setOf(icon(kind))),
        targets.mapValues { TargetRule(setOf(it.value)) },
        signals.mapValues { state(it.value) },
    )

    private fun repeated(baseId: String, label: FeatureKey, fixedRowOrder: List<String>): Map<String, TargetRule> =
        fixedRowOrder.mapIndexed { index, marker ->
            "$baseId#${index + 1}" to TargetRule(setOf(label), icon(marker))
        }.toMap()

    private fun text(value: String) = FeatureKey(FeatureKind.TEXT, value)
    private fun icon(value: String) = FeatureKey(FeatureKind.ICON, value)
    private fun state(value: String) = FeatureKey(FeatureKind.STATE, value)
}
