package com.hyojason.app.observer

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.ErrorDescriptor
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.StepDiagnostic
import com.hyojason.app.contract.StepStatus
import com.hyojason.app.observer.detection.PROTECTED_SCREEN_KIND
import com.hyojason.app.observer.detection.UNKNOWN_SCREEN_KIND

data class ScreenSample(
    val projectionEpoch: String,
    val capturedMonoMs: Long,
    val screenKind: String,
    val frameLayoutRevision: Long,
    val visibleTargets: List<LocalTarget>,
    val verifiedSignals: Set<String>,
    val health: ObservationHealth,
)

data class TransitionData(
    val screen_kind: String,
    val screen_revision: Long,
    val layout_revision: Long,
    val target_ids: Set<String>,
    val verified_signals: Set<String>,
    val observation_health: ObservationHealth,
)

data class Stabilization(
    val local: LocalObservation?,
    val result: NodeResult<TransitionData>?,
    val screenToPublish: ScreenState?,
    val locationInvalidated: Boolean,
    val verdictPending: Boolean,
    val fromScreenRevision: Long? = null,
)

/** One serialized instance per active projection epoch. */
class ScreenStabilizer(
    private val projectionEpoch: String,
    previousScreenRevision: Long = 0,
    previousLayoutRevision: Long = 0,
) {
    private data class Meaning(val kind: String, val evidence: Set<String>)

    private var screenRevision = previousScreenRevision
    private var layoutRevision = previousLayoutRevision
    private var lastCapturedMonoMs: Long? = null
    private var pendingMeaning: Meaning? = null
    private var frameLayoutRevision: Long? = null
    private var current: LocalObservation? = null

    fun observe(sample: ScreenSample, diagnostic: Diagnostic): Stabilization {
        val base = diagnostic.copy(node_id = "N-10", terminal_error = null)
        if (sample.projectionEpoch != projectionEpoch || diagnostic.basis.projection_epoch != projectionEpoch) {
            val error = ErrorDescriptor("STALE_BASIS", STEP, false)
            val failure = base.copy(
                terminal_error = error,
                steps = base.steps + (STEP to StepDiagnostic(StepStatus.FAILED, error.code)),
            )
            return Stabilization(null, NodeResult(NodeStatus.ERROR, null, null, failure), null, true, false)
        }
        if (sample.capturedMonoMs <= (lastCapturedMonoMs ?: -1)) {
            return Stabilization(null, null, null, true, pendingMeaning != null)
        }
        lastCapturedMonoMs = sample.capturedMonoMs
        if (sample.health == ObservationHealth.GAP || sample.health == ObservationHealth.PROTECTED) {
            return gap(sample.health, base)
        }
        if (sample.health != ObservationHealth.STABLE) {
            pendingMeaning = null
            return Stabilization(null, null, null, true, true)
        }

        val meaning = Meaning(sample.screenKind, sample.verifiedSignals.toSet())
        val previousMeaning = current?.screen?.let { Meaning(it.screen_kind, it.evidence_codes) }
        val needsConfirmation = meaning != previousMeaning || pendingMeaning != null
        if (needsConfirmation && meaning != pendingMeaning) {
            pendingMeaning = meaning
            return Stabilization(null, null, null, true, true)
        }
        pendingMeaning = null
        return confirm(sample, meaning, meaning != previousMeaning, base)
    }

    private fun gap(health: ObservationHealth, diagnostic: Diagnostic): Stabilization {
        require(health == ObservationHealth.PROTECTED || health == ObservationHealth.GAP)
        pendingMeaning = null
        current = null
        frameLayoutRevision = null
        val kind = if (health == ObservationHealth.PROTECTED) PROTECTED_SCREEN_KIND else UNKNOWN_SCREEN_KIND
        val screen = ScreenState(projectionEpoch, screenRevision, kind, emptySet(), emptySet(), health)
        return Stabilization(null, result("observation_gap", screen, diagnostic), screen, true, false)
    }

    private fun confirm(
        sample: ScreenSample,
        meaning: Meaning,
        semanticChanged: Boolean,
        diagnostic: Diagnostic,
    ): Stabilization {
        val previous = current
        val layoutChanged = previous == null ||
            sample.frameLayoutRevision != frameLayoutRevision ||
            geometry(sample.visibleTargets) != geometry(previous.targets)
        if (semanticChanged) screenRevision++
        if (layoutChanged) layoutRevision++

        frameLayoutRevision = sample.frameLayoutRevision
        val screen = ScreenState(
            projectionEpoch, screenRevision, meaning.kind,
            sample.visibleTargets.mapTo(mutableSetOf()) { it.target_id },
            meaning.evidence, ObservationHealth.STABLE,
        )
        val local = LocalObservation(screen, layoutRevision, sample.capturedMonoMs, sample.visibleTargets.toList())
        current = local
        val outcome = when {
            semanticChanged -> "screen_changed"
            layoutChanged -> "layout_changed"
            else -> null
        }
        val publish = semanticChanged || previous?.screen?.target_ids != screen.target_ids
        val fromScreenRevision = previous?.screen?.screen_revision?.takeIf { semanticChanged }
        return Stabilization(
            local = local,
            result = outcome?.let { result(it, screen, diagnostic) },
            screenToPublish = screen.takeIf { publish },
            locationInvalidated = layoutChanged || semanticChanged,
            verdictPending = fromScreenRevision != null,
            fromScreenRevision = fromScreenRevision,
        )
    }

    private fun result(outcome: String, screen: ScreenState, diagnostic: Diagnostic) = NodeResult(
        NodeStatus.SUCCESS, outcome, TransitionData(
            screen.screen_kind, screen.screen_revision, layoutRevision,
            screen.target_ids, screen.evidence_codes, screen.observation_health,
        ),
        diagnostic.copy(
            node_id = "N-10",
            terminal_error = null,
            steps = diagnostic.steps + (STEP to StepDiagnostic(StepStatus.SUCCESS, null)),
        ),
    )

    private fun geometry(targets: List<LocalTarget>) = targets.map { it.target_id to it.rect }.toSet()

    private companion object {
        const val STEP = "stabilize_screen"
    }
}
