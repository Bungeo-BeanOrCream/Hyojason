package com.hyojason.app.overlay.gate

import com.hyojason.app.observer.LocalObservation
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.LocalTarget
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.observer.ScreenState
import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.Source
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GuideGateTest {
    private val call = HelpMode.CALL_CHILD_LED
    private val postCall = HelpMode.POST_CALL_AGENT
    private val basis = Basis("pair", "call", "help", 9, 1, 1, 1, 1, "epoch", 3, 1)
    private val diagnostic = Diagnostic(
        "trace-fixture", "run-fixture", "N-19", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null,
    )
    private val targetId = "settings.display"
    private val rect = LocalRect(30, 120, 110, 180)
    private val evidence = setOf("PROFILE_MATCH")

    @Test
    fun waitsBeforeDeadlineOrWithoutVerifiedEvidence() {
        assertEquals("waiting", evaluate(at(2_999)).outcome)
        assertEquals("waiting", evaluate(at(3_000).copy(inputEvidence = InputEvidence.UNKNOWN)).outcome)
        assertNull(evaluate(at(3_000).copy(local_observation = null)).data?.display)
    }

    @Test
    fun correctAt2999Or3000ShowsNothing() {
        for (time in listOf(2_999L, 3_000L)) {
            val result = evaluate(at(time).copy(verdict = Scoped(ObservedVerdict(Verdict.CORRECT, time), basis)))
            assertEquals("followed", result.outcome)
            assertNull(result.data?.display)
            assertEquals(true, result.data?.cancel_timer)
        }
    }

    @Test
    fun tapAt3000And3001UsesUniqueCurrentRect() {
        for (time in listOf(3_000L, 3_001L)) {
            val input = at(time)
            val result = evaluate(input)
            assertEquals("intervene_tap", result.outcome)
            assertEquals(LocalGuide(GuideAction.TAP, rect), result.data?.display)
            val observation = requireNotNull(input.local_observation)
            val conflicting = observation.copy(targets = observation.targets + observation.targets)
            assertEquals("waiting", evaluate(input.copy(local_observation = conflicting)).outcome)
        }
    }

    @Test
    fun wrongRequiresVerifiedReverseEdgeAndBeatsBelowFold() {
        val input = at(3_000).copy(
            target = scopedTarget(TargetLocation.BELOW_FOLD),
            verdict = Scoped(ObservedVerdict(Verdict.WRONG, 2_400), basis),
            recovery = Scoped(VerifiedRecovery("verified-edge"), basis),
        )
        assertEquals("waiting", evaluate(input.copy(now_mono_ms = 2_999,
            local_observation = at(2_999).local_observation)).outcome)
        assertEquals("waiting", evaluate(input.copy(recovery = null)).outcome)
        assertEquals("intervene_back", evaluate(input).outcome)
        assertEquals(GuideAction.BACK, evaluate(input).data?.display?.action)
    }

    @Test
    fun scrollRequiresBelowFoldEvidence() {
        val input = at(3_000).copy(target = scopedTarget(TargetLocation.BELOW_FOLD))
        assertEquals("intervene_scroll", evaluate(input).outcome)
        assertEquals(GuideAction.SCROLL_DOWN, evaluate(input).data?.display?.action)
        assertEquals("waiting", evaluate(input.copy(target = scopedTarget(TargetLocation.BELOW_FOLD, emptySet()))).outcome)
    }

    @Test
    fun postCallRequiresFullDelayAndAnUnexpiredCurrentProposal() {
        val latest = basis.copy(mode_revision = 2, instruction_revision = null)
        val proposal = PostCallProposal(GuideAction.TAP, targetId, "tap_display", 1_000, 4_000)
        val input = at(4_000).copy(mode = postCall, basis = latest, latestBasis = latest,
            target = null, t0_mono_ms = 1_000, proposal = Scoped(proposal, latest))
        assertEquals("post_call_guide", evaluate(input).outcome)
        assertEquals(LocalGuide(GuideAction.TAP, rect), evaluate(input).data?.display)
        assertEquals("waiting", evaluate(input.copy(t0_mono_ms = 1_001)).outcome)
        assertEquals("waiting", evaluate(input.copy(proposal = Scoped(proposal.copy(validForMs = 3_000), latest))).outcome)
    }

    @Test
    fun policyDenialOnlyReturnsWarningAndProtectedScreenClearsIt() {
        val input = at(100).copy(policyDenied = true)
        val result = evaluate(input)
        assertEquals("policy_denied", result.outcome)
        assertEquals(LocalGuide(null, message = "되돌리기 어려운 동작이에요. 천천히 확인하세요"), result.data?.display)
        val observation = requireNotNull(input.local_observation)
        val protected = observation.copy(screen = observation.screen.copy(observation_health = ObservationHealth.PROTECTED))
        assertEquals("PROTECTED_SCREEN", evaluate(input.copy(local_observation = protected)).diagnostics.terminal_error?.code)
        assertNull(evaluate(input.copy(local_observation = protected)).data)
    }

    @Test
    fun staleBasisOrLayoutReturnsErrorWithoutDisplay() {
        val input = at(3_000)
        for (invalid in listOf(
            input.copy(latestBasis = basis.copy(instruction_revision = 2)),
            input.copy(latestLayoutRevision = 2),
        )) {
            val result = evaluate(invalid)
            assertEquals(NodeStatus.ERROR, result.status)
            assertEquals("STALE_BASIS", result.diagnostics.terminal_error?.code)
            assertNull(result.outcome)
            assertNull(result.data)
        }
        assertEquals("NO_CONSENT", evaluate(input.copy(consent = false)).diagnostics.terminal_error?.code)
        assertEquals("intervene_tap", evaluate(input.copy(latestBasis = basis.copy(event_seq = 99))).outcome)
    }

    @Test
    fun pendingAt2999And3000ThenCorrectAt3050NeverShows() {
        for (time in listOf(2_999L, 3_000L)) {
            val result = evaluate(at(time).copy(inputEvidence = InputEvidence.PENDING))
            assertEquals("waiting", result.outcome)
            assertNull(result.data?.display)
        }
        val result = evaluate(at(3_050).copy(
            inputEvidence = InputEvidence.PENDING, verdict = Scoped(ObservedVerdict(Verdict.CORRECT, 2_999), basis),
        ))
        assertEquals("followed", result.outcome)
        assertNull(result.data?.display)
    }

    @Test
    fun newInstructionAndModeDiscardPreviousTimerAndOldProposal() {
        val clock = GateClock()
        assertTrue(clock.applyMode("help", call, 1))
        assertTrue(clock.acceptInstruction("first", 1, 100))
        assertFalse(clock.acceptInstruction("first", 1, 200))
        assertFalse(clock.stableScreen("epoch", 3, 250))
        assertEquals(100L, clock.t0_mono_ms)
        assertTrue(clock.acceptInstruction("second", 2, 300))
        assertFalse(clock.acceptInstruction("first", 1, 400))
        assertEquals(300L, clock.t0_mono_ms)
        assertTrue(clock.applyMode("help", postCall, 2))
        assertNull(clock.t0_mono_ms)
        assertTrue(clock.stableScreen("epoch", 3, 500))
        assertFalse(clock.stableScreen("epoch", 3, 600))
        assertEquals(500L, clock.t0_mono_ms)
        assertTrue(clock.stableScreen("epoch", 4, 700))
        assertEquals(700L, clock.t0_mono_ms)
        val latest = basis.copy(mode_revision = 2)
        val result = evaluate(at(4_000).copy(
            mode = postCall, basis = latest, latestBasis = latest, target = null, t0_mono_ms = clock.t0_mono_ms,
            proposal = Scoped(PostCallProposal(GuideAction.TAP, targetId, "tap_display", 1_000, 5_000), basis),
        ))
        assertEquals("STALE_BASIS", result.diagnostics.terminal_error?.code)
        assertNull(result.data)
        var previous: LocalGuide? = LocalGuide(GuideAction.TAP, rect)
        val parent = suppressStaleBasis(result, diagnostic) { previous = null }
        assertNull(previous)
        assertEquals("suppressed", parent?.outcome)
        assertEquals(ParentGuideData(null, false, "STALE_BASIS"), parent?.data)
        clock.clear()
        assertNull(clock.t0_mono_ms)
    }

    @Test
    fun frameOlderThanBudgetIsCaptureGapButMeasuredLatencyIsNot() {
        val input = at(3_000)
        val captured = { age: Long -> input.copy(local_observation = input.local_observation!!.copy(
            captured_mono_ms = 3_000 - age)) }
        assertEquals("intervene_tap", evaluate(captured(401)).outcome)
        assertEquals("intervene_tap", evaluate(captured(GuideGate.FRAME_MAX_AGE_MS)).outcome)
        val gap = evaluate(captured(GuideGate.FRAME_MAX_AGE_MS + 1))
        assertEquals("CAPTURE_GAP", gap.diagnostics.terminal_error?.code)
        assertNull(gap.data)
    }

    private fun at(time: Long) = GateInput(
        mode = call, basis = basis, latestBasis = basis, latestLayoutRevision = 1,
        now_mono_ms = time, t0_mono_ms = 0,
        local_observation = LocalObservation(
            ScreenState("epoch", 3, "settings_home", setOf(targetId), evidence, ObservationHealth.STABLE),
            1, time, listOf(LocalTarget(targetId, rect, evidence)),
        ),
        inputEvidence = InputEvidence.CONTINUOUS_NO_CHANGE,
        target = scopedTarget(TargetLocation.VISIBLE),
    )

    private fun scopedTarget(location: TargetLocation, codes: Set<String> = evidence) =
        Scoped(CurrentTarget(targetId, location, codes), basis)

    private fun evaluate(input: GateInput) = GuideGate.evaluate(input, diagnostic)
}
