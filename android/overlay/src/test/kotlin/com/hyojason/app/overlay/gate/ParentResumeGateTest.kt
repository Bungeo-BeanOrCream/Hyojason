package com.hyojason.app.overlay.gate

import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.LocalObservation
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.observer.ScreenState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ParentResumeGateTest {
    private val latest = Basis(
        pair_id = "pair", call_id = "call", help_id = "help", event_seq = 14,
        consent_revision = 1, goal_revision = 2, mode_revision = 2, instruction_revision = null,
        projection_epoch = "epoch", screen_revision = 6, utterance_seq = null,
    )
    private val clock = GateClock()
    private val effects = mutableListOf<String>()
    private val resume = ParentResumeGate(
        clock,
        clearDisplay = { effects += "overlay" },
        cancelScheduledTimer = {
            assertNull(clock.t0_mono_ms)
            effects += "timer"
        },
        clearHelpCache = { effects += "cache" },
    )

    @Test
    fun n13_1_reconnectRequestsFreshObservationAndPreservesInstructionReplayProtection() {
        assertTrue(clock.applyMode("help", HelpMode.CALL_CHILD_LED, 1))
        assertTrue(clock.acceptInstruction("instruction", 1, 100))
        val call = latest.copy(mode_revision = 1, instruction_revision = 1)
        assertTrue(resume.applyReconnect(ParentReconnect(14, call, true, 1_000), "help"))
        assertEquals(listOf("overlay", "timer"), effects)
        assertNull(clock.t0_mono_ms)
        assertFalse(clock.acceptInstruction("instruction", 2, 1_100))
        assertNull(clock.t0_mono_ms)
        assertTrue(clock.acceptInstruction("new_instruction", 2, 1_200))
        assertEquals(1_200L, clock.t0_mono_ms)
    }

    @Test
    fun n13_2_missingHelpClearsCacheAndCannotShowGuidance() {
        assertFalse(resume.applyReconnect(ParentReconnect(14, null, false, 1_000), "help"))
        assertEquals(listOf("overlay", "timer", "cache"), effects)
        assertFalse(resume.onScreenObserved(screen(1_100), latest))
        assertNull(evaluate(proposalBasis()).data)
    }

    @Test
    fun n13_1_switchedHelpClearsPreviousCacheAndRejectsPriorHelp() {
        assertTrue(clock.applyMode("old-help", HelpMode.POST_CALL_AGENT, 1))
        assertTrue(clock.stableScreen("old-epoch", 4, 100))
        assertTrue(resume.applyReconnect(ParentReconnect(14, latest, true, 1_000), "old-help"))
        assertEquals(listOf("overlay", "timer", "cache"), effects)
        assertFalse(resume.onScreenObserved(screen(1_100), latest.copy(help_id = "old-help")))
        assertTrue(resume.onScreenObserved(screen(1_100), latest))
        assertStale(proposalBasis().copy(help_id = "old-help"))
        assertEquals("post_call_guide", evaluate(proposalBasis()).outcome)
    }

    @Test
    fun n13_4_staleRevisionsAndEventSequenceAtReconnectBoundaryAreRejected() {
        resume.applyReconnect(ParentReconnect(14, latest, true, 1_000), "help")
        assertEquals("waiting", evaluate(proposalBasis()).outcome)
        assertTrue(resume.onScreenObserved(screen(1_100), latest))
        for (sequence in listOf(null, 13L, 14L)) assertStale(proposalBasis(sequence))
        assertStale(proposalBasis().copy(goal_revision = 1))
        assertStale(proposalBasis().copy(mode_revision = 1))
        assertStale(proposalBasis().copy(instruction_revision = 1))
        assertEquals("post_call_guide", evaluate(proposalBasis()).outcome)
    }

    @Test
    fun n13_1_staleOrUnstableObservationCannotReleaseBarrier() {
        resume.applyReconnect(ParentReconnect(14, latest, true, 1_000), "help")
        assertFalse(resume.onScreenObserved(screen(1_000), latest))
        assertFalse(resume.onScreenObserved(screen(1_100, ObservationHealth.GAP), latest))
        assertFalse(resume.onScreenObserved(screen(1_100, epoch = "old-epoch"), latest))
        assertFalse(resume.onScreenObserved(screen(1_100), latest.copy(event_seq = 13)))
        assertFalse(resume.onScreenObserved(screen(1_100), latest.copy(goal_revision = 1)))
        assertEquals("waiting", evaluate(proposalBasis()).outcome)
        assertTrue(resume.onScreenObserved(screen(3_950), latest))
        assertFalse(resume.onScreenObserved(screen(3_949), latest))
        assertEquals("waiting", evaluate(proposalBasis(), screen(3_949)).outcome)
        assertEquals("post_call_guide", evaluate(proposalBasis()).outcome)
    }

    @Test
    fun n13_1_optionalReobserveRequestStillCannotReusePriorObservation() {
        assertFalse(resume.applyReconnect(ParentReconnect(14, latest, false, 1_000), "help"))
        assertEquals("waiting", evaluate(proposalBasis()).outcome)
        assertTrue(resume.onScreenObserved(screen(1_100), latest))
        assertEquals("post_call_guide", evaluate(proposalBasis()).outcome)
    }

    private fun proposalBasis(sequence: Long? = 15) = latest.copy(event_seq = sequence)

    private fun screen(
        capturedMonoMs: Long,
        health: ObservationHealth = ObservationHealth.STABLE,
        epoch: String = "epoch",
    ) = LocalObservation(
        ScreenState(epoch, 6, "settings_home", emptySet(), emptySet(), health),
        1, capturedMonoMs, emptyList(),
    )

    private fun evaluate(candidate: Basis, observation: LocalObservation = screen(4_000)) = GuideGate.evaluate(
        GateInput(
            mode = HelpMode.POST_CALL_AGENT, basis = latest, latestBasis = latest, latestLayoutRevision = 1,
            now_mono_ms = 4_000, t0_mono_ms = 1_000, local_observation = observation,
            inputEvidence = InputEvidence.CONTINUOUS_NO_CHANGE,
            proposal = Scoped(PostCallProposal(GuideAction.BACK, null, "back", 3_900, 500), candidate),
        ),
        Diagnostic("trace", "run", "N-19", Source.FIXTURE, latest, emptyList(), 0, emptyMap(), null),
        resume,
    )

    private fun assertStale(candidate: Basis) {
        val result = evaluate(candidate)
        assertEquals("STALE_BASIS", result.diagnostics.terminal_error?.code)
        assertNull(result.data)
    }
}
