package com.hyojason.app.observer

enum class ObservationHealth { STABLE, UNSTABLE, PROTECTED, GAP }

data class ScreenState(
    val projection_epoch: String,
    val screen_revision: Long,
    val screen_kind: String,
    val target_ids: Set<String>,
    val evidence_codes: Set<String>,
    val observation_health: ObservationHealth,
)

data class LocalRect(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    init {
        require(left >= 0 && top >= 0 && right > left && bottom > top)
    }
}

data class LocalTarget(val target_id: String, val rect: LocalRect, val evidence_codes: Set<String>)

data class LocalObservation(
    val screen: ScreenState,
    val layout_revision: Long,
    val captured_mono_ms: Long,
    val targets: List<LocalTarget>,
)
