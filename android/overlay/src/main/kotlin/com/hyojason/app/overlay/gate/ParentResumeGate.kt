package com.hyojason.app.overlay.gate

import com.hyojason.app.contract.Basis
import com.hyojason.app.observer.LocalObservation
import com.hyojason.app.observer.ObservationHealth

data class ParentReconnect(
    val serverEventSeq: Long,
    val latestBasis: Basis?,
    val mustReobserve: Boolean,
    val appliedMonoMs: Long,
) {
    init {
        require(serverEventSeq >= 0 && appliedMonoMs >= 0)
    }
}

internal enum class ResumeCheck { ALLOW, WAIT_FOR_OBSERVATION, STALE }

/** Parent-local N-13 state, owned by the same serialized loop as [GateClock]. */
class ParentResumeGate(
    private val clock: GateClock,
    private val clearDisplay: () -> Unit,
    private val cancelScheduledTimer: () -> Unit,
    private val clearHelpCache: () -> Unit,
) {
    private var reconnect: ParentReconnect? = null
    private var reobserved: ObservedScreen? = null

    private data class ObservedScreen(val epoch: String, val revision: Long, val capturedMonoMs: Long)

    /** Returns whether the caller should actively request reobservation. */
    fun applyReconnect(response: ParentReconnect, previousHelpId: String?): Boolean {
        clearDisplay()
        clock.cancelTimer()
        cancelScheduledTimer()
        reconnect = response
        reobserved = null
        val resumedHelp = response.latestBasis?.help_id
        if (resumedHelp == null || (previousHelpId != null && previousHelpId != resumedHelp)) {
            clock.clear()
            clearHelpCache()
        }
        return resumedHelp != null && response.mustReobserve
    }

    /** Call only after sending a new stable observation with the current local basis. */
    fun onScreenObserved(observation: LocalObservation, latestBasis: Basis): Boolean {
        val response = reconnect ?: return false
        if (!isResumedScope(latestBasis, response) ||
            observation.screen.observation_health != ObservationHealth.STABLE ||
            observation.captured_mono_ms <= response.appliedMonoMs ||
            observation.screen.projection_epoch != latestBasis.projection_epoch ||
            observation.screen.screen_revision != latestBasis.screen_revision
        ) return false
        val previous = reobserved
        if (previous != null && (observation.captured_mono_ms < previous.capturedMonoMs ||
                observation.screen.screen_revision < previous.revision)
        ) return false
        reobserved = ObservedScreen(
            observation.screen.projection_epoch, observation.screen.screen_revision, observation.captured_mono_ms,
        )
        return true
    }

    internal fun check(input: GateInput): ResumeCheck {
        val response = reconnect ?: return ResumeCheck.ALLOW
        if (!isResumedScope(input.latestBasis, response)) return ResumeCheck.STALE
        val observed = reobserved ?: return ResumeCheck.WAIT_FOR_OBSERVATION
        val current = input.local_observation ?: return ResumeCheck.WAIT_FOR_OBSERVATION
        if (current.screen.projection_epoch != observed.epoch ||
            current.screen.screen_revision < observed.revision ||
            current.captured_mono_ms < observed.capturedMonoMs
        ) return ResumeCheck.WAIT_FOR_OBSERVATION

        val inputs = listOfNotNull(input.target, input.verdict, input.recovery, input.proposal)
        val candidateBases = inputs.map { it.basis } + listOfNotNull(input.basis.takeIf { input.policyDenied })
        return if (candidateBases.all { freshCandidate(it, input.latestBasis, response.serverEventSeq) }) {
            ResumeCheck.ALLOW
        } else ResumeCheck.STALE
    }

    private fun isResumedScope(latest: Basis, response: ParentReconnect): Boolean {
        val resumed = response.latestBasis ?: return false
        val sequence = latest.event_seq ?: return false
        if (resumed.help_id == null || resumed.help_id != latest.help_id || resumed.pair_id != latest.pair_id ||
            sequence < response.serverEventSeq
        ) return false
        if (sequence == response.serverEventSeq) {
            return latest.mode_revision == resumed.mode_revision &&
                latest.goal_revision == resumed.goal_revision &&
                latest.instruction_revision == resumed.instruction_revision
        }
        return notOlder(latest.mode_revision, resumed.mode_revision) &&
            notOlder(latest.goal_revision, resumed.goal_revision) &&
            notOlder(latest.instruction_revision, resumed.instruction_revision)
    }

    // A newer event may explicitly clear a goal or instruction from the current basis.
    private fun notOlder(current: Long?, resumed: Long?): Boolean =
        current == null || resumed == null || current >= resumed

    private fun freshCandidate(candidate: Basis, latest: Basis, cutoff: Long): Boolean =
        (candidate.event_seq ?: -1) > cutoff &&
            candidate.help_id == latest.help_id &&
            candidate.mode_revision == latest.mode_revision &&
            candidate.goal_revision == latest.goal_revision &&
            candidate.instruction_revision == latest.instruction_revision
}
