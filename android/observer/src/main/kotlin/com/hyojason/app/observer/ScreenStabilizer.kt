package com.hyojason.app.observer

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.ErrorDescriptor
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.StepDiagnostic
import com.hyojason.app.contract.StepStatus

/** Semantic controls come from the profile. They can remain the same as visible targets scroll out. */
data class SemanticScreen(val kind: String, val controlIds: Set<String>)

data class ScreenSample(
    val projectionEpoch: String,
    val capturedMonoMs: Long,
    val semantic: SemanticScreen,
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
)

/** One serialized instance per active projection epoch. */
class ScreenStabilizer(
    private val projectionEpoch: String,
    previousScreenRevision: Long = 0,
    previousLayoutRevision: Long = 0,
) {
    private var screenRevision = previousScreenRevision
    private var layoutRevision = previousLayoutRevision
    private var lastCapturedMonoMs: Long? = null
    private var stableSemantic: SemanticScreen? = null
    private var pendingSemantic: SemanticScreen? = null
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
            return Stabilization(null, null, null, true, pendingSemantic != null)
        }
        lastCapturedMonoMs = sample.capturedMonoMs
        if (sample.health == ObservationHealth.GAP || sample.health == ObservationHealth.PROTECTED) {
            return gap(sample.health, base)
        }
        if (sample.health != ObservationHealth.STABLE) {
            pendingSemantic = null
            return Stabilization(null, null, null, true, true)
        }

        val hasBaseline = current != null && pendingSemantic == null
        val needsConfirmation = !hasBaseline || sample.semantic != stableSemantic
        if (needsConfirmation && sample.semantic != pendingSemantic) {
            pendingSemantic = sample.semantic
            return Stabilization(null, null, null, true, true)
        }
        pendingSemantic = null
        return confirm(sample, base)
    }

    private fun gap(health: ObservationHealth, diagnostic: Diagnostic): Stabilization {
        require(health == ObservationHealth.PROTECTED || health == ObservationHealth.GAP)
        pendingSemantic = null
        current = null
        frameLayoutRevision = null
        val screen = ScreenState(projectionEpoch, screenRevision, "unknown", emptySet(), emptySet(), health)
        return Stabilization(null, result("observation_gap", screen, diagnostic), screen, true, false)
    }

    private fun confirm(sample: ScreenSample, diagnostic: Diagnostic): Stabilization {
        val previous = current
        val semanticChanged = sample.semantic != stableSemantic
        val layoutChanged = previous == null || semanticChanged ||
            sample.frameLayoutRevision != frameLayoutRevision ||
            geometry(sample.visibleTargets) != geometry(previous.targets)
        if (semanticChanged) screenRevision++
        if (layoutChanged) layoutRevision++

        stableSemantic = sample.semantic
        frameLayoutRevision = sample.frameLayoutRevision
        val screen = ScreenState(
            projectionEpoch, screenRevision, sample.semantic.kind,
            sample.visibleTargets.mapTo(mutableSetOf()) { it.target_id },
            sample.verifiedSignals, ObservationHealth.STABLE,
        )
        val local = LocalObservation(screen, layoutRevision, sample.capturedMonoMs, sample.visibleTargets.toList())
        current = local
        val outcome = when {
            semanticChanged -> "screen_changed"
            layoutChanged -> "layout_changed"
            else -> null
        }
        val publish = semanticChanged || previous == null || previous.screen.evidence_codes != screen.evidence_codes
        return Stabilization(
            local, outcome?.let { result(it, screen, diagnostic) },
            screen.takeIf { publish }, layoutChanged, semanticChanged && previous != null,
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
