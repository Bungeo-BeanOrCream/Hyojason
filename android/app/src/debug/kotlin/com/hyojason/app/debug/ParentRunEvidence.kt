package com.hyojason.app.debug

import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.TransitionData
import com.hyojason.app.overlay.gate.GateClock
import com.hyojason.app.overlay.gate.GuideAction
import com.google.gson.GsonBuilder

private val token = Regex("[A-Za-z0-9][A-Za-z0-9_.:#/+~-]{0,127}")
private val phone = Regex("(?:\\+?[0-9][-.]?){7,}")

private fun checkedId(value: String): String = value.also {
    require(token.matches(it) && !phone.matches(it) && "://" !in it)
}

internal enum class FailureDomain { FUNCTION, DEVICE, CONTRACT }

internal data class ParentRun(
    val caseId: String, val attempt: Int, val traceId: String, val source: Source,
    val basis: Basis,
    val inputSource: Source? = null, val screenSource: Source? = null, val modelSource: Source? = null,
    val deviceRevision: String? = null, val appRevision: String? = null,
    val profileRevision: String? = null, val modelRevision: String? = null,
    val failureDomain: FailureDomain? = null,
) {
    init {
        require(attempt >= 1)
        listOfNotNull(caseId, traceId, deviceRevision, appRevision, profileRevision, modelRevision,
            basis.pair_id, basis.call_id, basis.help_id, basis.projection_epoch).forEach(::checkedId)
    }

    fun location(boundsMatch: Boolean?) = row(buildMap {
        boundsMatch?.let { put("bounds_match", it) }
    })

    fun timing(measurement: ParentTiming) = row(measurement.actual())

    /** Compare N-10 snapshots only within a continuous, stable projection. */
    fun scrollTransition(before: NodeResult<TransitionData>, after: NodeResult<TransitionData>): Map<String, Any?> {
        require(caseId == "E09")
        val previous = requireNotNull(before.data)
        val current = requireNotNull(after.data)
        for (result in listOf(before, after)) {
            val diagnostic = result.diagnostics
            require(diagnostic.node_id == "N-10" && result.outcome in setOf("screen_changed", "layout_changed"))
            require(diagnostic.basis.projection_epoch != null && diagnostic.basis.projection_epoch == basis.projection_epoch)
            require(diagnostic.basis.help_id == basis.help_id)
        }
        require(after.diagnostics.trace_id == traceId && current.screen_revision == basis.screen_revision)
        val screens = before.diagnostics.source.withInputs(listOf(after.diagnostics.source))
        return copy(screenSource = screens.withInputs(listOfNotNull(screenSource))).row(mapOf(
            "screen_revision_changed" to (previous.screen_revision != current.screen_revision),
            "layout_revision_changed" to (previous.layout_revision != current.layout_revision),
        ))
    }

    private fun row(actual: Map<String, Any?>): Map<String, Any?> = mapOf(
        "case_id" to caseId, "attempt" to attempt, "trace_id" to traceId,
        "source" to source.withInputs(listOfNotNull(inputSource, screenSource, modelSource)).name,
        "input_source" to inputSource?.name, "screen_source" to screenSource?.name, "model_source" to modelSource?.name,
        "device_revision" to deviceRevision, "app_revision" to appRevision,
        "profile_revision" to profileRevision, "model_revision" to modelRevision,
        "failure_domain" to failureDomain?.name, "actual" to actual,
        "basis" to mapOf(
            "pair_id" to basis.pair_id, "call_id" to basis.call_id, "help_id" to basis.help_id,
            "event_seq" to basis.event_seq, "consent_revision" to basis.consent_revision,
            "goal_revision" to basis.goal_revision, "mode_revision" to basis.mode_revision,
            "instruction_revision" to basis.instruction_revision, "projection_epoch" to basis.projection_epoch,
            "screen_revision" to basis.screen_revision, "utterance_seq" to basis.utterance_seq,
        ),
    )
}

/** Start at gate application. Supply the parent monotonic clock used to drive GateClock.
 * Call rendered() only when the guide actually appears, then export at the observation cutoff.
 */
internal enum class ParentObservation { CORRECT, WRONG, PENDING, NONE }

internal class ParentTiming(gate: GateClock, mode: HelpMode, private val nowMs: () -> Long) {
    private val start = gate.t0_mono_ms
    private val origin = when (mode) {
        HelpMode.CALL_CHILD_LED -> "instruction_received"
        HelpMode.POST_CALL_AGENT -> "post_call_stable_screen"
    }
    private var firstRender: Long? = null
    private var count = 0
    private var firstAction: GuideAction? = null
    private var observation: ParentObservation? = null
    private var firstObserved: Long? = null
    private var safeToShow: Boolean? = null

    fun observed(value: ParentObservation) {
        if (value != ParentObservation.NONE && firstObserved == null) firstObserved = nowMs()
        require(value != ParentObservation.NONE || firstObserved == null)
        observation = value
    }

    fun checkedSafety(safe: Boolean) { safeToShow = safe }

    fun rendered(action: GuideAction) {
        if (firstRender == null) {
            firstRender = nowMs()
            firstAction = action
        }
        count++
    }

    fun actual(): Map<String, Any?> = buildMap {
        put("clock_id", "parent_monotonic")
        put("origin", origin)
        start?.let { put("t0_ms", it) }
        put("checked_ms", nowMs())
        put("guide_count", count)
        put("render_ms", firstRender)
        put("action", firstAction?.name)
        observation?.let {
            put("observation", it.name.lowercase())
            put("first_observed_ms", firstObserved)
        }
        safeToShow?.let { put("safe_to_show", it) }
    }
}

/** D's case-specific actual schema has no fields for layout_revision or evidence_codes.
 * Keep the N-10 projection local until the shared contract includes them.
 */
internal fun TransitionData.parentTransition() = mapOf(
    "screen_revision" to screen_revision,
    "layout_revision" to layout_revision,
    "evidence_codes" to verified_signals.sorted(),
)

private val evidenceJson = GsonBuilder().serializeNulls().create()

internal fun observedRunsJson(run: ParentRun, measurement: ParentTiming): String =
    evidenceJson.toJson(mapOf("observed_runs" to listOf(run.timing(measurement))))

internal fun observedLocationJson(run: ParentRun, boundsMatch: Boolean?): String =
    evidenceJson.toJson(mapOf("observed_runs" to listOf(run.location(boundsMatch))))

internal fun observedScrollJson(run: ParentRun, before: NodeResult<TransitionData>, after: NodeResult<TransitionData>): String =
    evidenceJson.toJson(mapOf("observed_runs" to listOf(run.scrollTransition(before, after))))
