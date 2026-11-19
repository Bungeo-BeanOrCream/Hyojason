package com.hyojason.app.observer.detection

/** Explicitly scoped to the synthetic fixtures until labels and layout are checked on the device. */
object DemoProfiles {
    val fixture = DetectionProfile(
        "demo-ko-fixture", 1, listOf(
            profile("home", setOf("홈"), mapOf("home.settings" to "설정", "home.clock" to "시계")),
            profile("settings_home", setOf("설정", "네트워크 및 인터넷"), mapOf("settings.display" to "디스플레이")),
            profile("settings_display", setOf("디스플레이", "밝기 수준"), mapOf(
                "settings.display_size_text" to "표시 크기 및 텍스트",
                "settings.screen_timeout" to "화면 자동 꺼짐",
            )),
            profile("settings_display_size_text", setOf("표시 크기 및 텍스트", "미리보기"), emptyMap()),
            profile("settings_screen_timeout", setOf("화면 자동 꺼짐", "1분"), mapOf("settings.timeout.10m" to "10분"),
                mapOf("TIMEOUT_10M_SELECTED" to "timeout_10m_selected")),
            ProfileScreen(
                "clock_alarm_list",
                listOf(setOf(text("알람"), text("스톱워치")), setOf(icon("clock_alarm_list"))),
                mapOf("clock.alarm_tab" to setOf(text("알람")), "clock.add_alarm" to setOf(icon("add_alarm"))),
                mapOf("ACTIVE_ALARM_CARD" to state("active_alarm_card")),
            ),
            profile("clock_alarm_edit", setOf("알람 편집"), mapOf("clock.save_alarm" to "저장")),
            profile("maps_home", setOf("Google 지도"), mapOf("maps.search_box" to "여기에서 검색")),
            profile("maps_place_detail", setOf("개요", "리뷰"), mapOf("maps.directions" to "길찾기")),
            profile("messages_list", setOf("메시지"), emptyMap()),
            profile("messages_compose", setOf("새 대화"), emptyMap()),
            profile("gmail_inbox", setOf("기본", "받은편지함"), mapOf("gmail.inbox" to "받은편지함")),
            ProfileScreen("gmail_mail_detail", listOf(setOf(icon("mail_detail"))), emptyMap(),
                mapOf("MAIL_DETAIL_OPEN" to state("demo_mail_open"))),
        ),
    )

    private fun profile(
        kind: String,
        anchors: Set<String>,
        targets: Map<String, String>,
        signals: Map<String, String> = emptyMap(),
    ) = ProfileScreen(
        kind, listOf(anchors.mapTo(mutableSetOf(), ::text)),
        targets.mapValues { setOf(text(it.value)) },
        signals.mapValues { state(it.value) },
    )

    private fun text(value: String) = FeatureKey(FeatureKind.TEXT, value)
    private fun icon(value: String) = FeatureKey(FeatureKind.ICON, value)
    private fun state(value: String) = FeatureKey(FeatureKind.STATE, value)
}
