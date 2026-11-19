package com.hyojason.app.contract

data class Basis(
    val pair_id: String?,
    val call_id: String?,
    val help_id: String?,
    val event_seq: Long?,
    val consent_revision: Long?,
    val goal_revision: Long?,
    val mode_revision: Long?,
    val instruction_revision: Long?,
    val projection_epoch: String?,
    val screen_revision: Long?,
    val utterance_seq: Long?,
) {
    init {
        val revisions = listOf(
            event_seq, consent_revision, goal_revision, mode_revision,
            instruction_revision, screen_revision, utterance_seq,
        )
        require(revisions.all { it == null || it >= 0 }) { "Basis revisions must be nonnegative" }
    }
}

enum class HelpMode {
    CALL_CHILD_LED,
    POST_CALL_AGENT,
}
