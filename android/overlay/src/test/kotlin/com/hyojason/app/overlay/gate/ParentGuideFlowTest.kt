package com.hyojason.app.overlay.gate

import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.LocalObservation
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.LocalTarget
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.observer.ScreenState
import com.hyojason.app.overlay.surface.SurfaceHost
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ParentGuideFlowTest {
    private val basis = Basis("pair", "call", "help", 9, 1, 1, 1, 1, "epoch", 3, 1)
    private val diagnostic = Diagnostic("trace-fixture", "run-fixture", "B-06", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null)
    private val id = "settings.display"
    private val rect = LocalRect(30, 120, 110, 180)
    private val codes = setOf("PROFILE_MATCH")

    private class Host : SurfaceHost {
        var border: LocalRect? = null
        var banner: String? = null
        var borders = 0
        override fun showBorder(rect: LocalRect): Boolean { border = rect; borders++; return true }
        override fun showBanner(text: String): Boolean { banner = text; return true }
        override fun clear() { border = null; banner = null }
    }

    private fun at(time: Long, health: ObservationHealth = ObservationHealth.STABLE) = GateInput(
        mode = HelpMode.CALL_CHILD_LED, basis = basis, latestBasis = basis, latestLayoutRevision = 1,
        now_mono_ms = time, t0_mono_ms = 0,
        local_observation = LocalObservation(
            ScreenState("epoch", 3, "settings_home", setOf(id), codes, health), 1, time,
            listOf(LocalTarget(id, rect, codes)),
        ),
        inputEvidence = InputEvidence.CONTINUOUS_NO_CHANGE,
        target = Scoped(CurrentTarget(id, TargetLocation.VISIBLE, codes), basis),
    )

    private fun run(input: GateInput, host: Host = Host()) =
        ParentGuideFlow.run(input, diagnostic, host, true)

    @Test fun b06_1_noBorderBeforeDeadlineOrWhenFollowed() {
        for (input in listOf(
            at(2_999),
            at(3_000).copy(verdict = Scoped(ObservedVerdict(Verdict.CORRECT, 3_000), basis)),
            at(3_000).copy(inputEvidence = InputEvidence.PENDING),
        )) {
            val host = Host()
            val result = run(input, host)
            assertEquals("suppressed", result.outcome)
            assertNull(host.border)
            assertNull(host.banner)
            assertEquals("B-06", result.diagnostics.node_id)
        }
    }

    @Test fun b06_2_tapBackScrollEachShowOnce() {
        val tap = Host()
        assertEquals("shown", run(at(3_000), tap).outcome)
        assertEquals(rect, tap.border)
        assertEquals(1, tap.borders)

        val back = Host()
        run(at(3_000).copy(
            verdict = Scoped(ObservedVerdict(Verdict.WRONG, 2_400), basis),
            recovery = Scoped(VerifiedRecovery("edge"), basis)), back)
        assertEquals("뒤로 가기", back.banner)
        assertNull(back.border)

        val scroll = Host()
        run(at(3_000).copy(target = Scoped(CurrentTarget(id, TargetLocation.BELOW_FOLD, codes), basis)), scroll)
        assertEquals("화면 내리기", scroll.banner)
    }

    @Test fun staleOrProtectedClearsPreviousDisplay() {
        val host = Host()
        run(at(3_000), host)
        val protected = run(at(3_100, ObservationHealth.PROTECTED), host)
        assertEquals("suppressed", protected.outcome)
        assertEquals("PROTECTED_SCREEN", protected.data?.reason)
        assertNull(host.border)
        assertNull(host.banner)

        run(at(3_000), host)
        val stale = run(at(3_000).copy(latestLayoutRevision = 2), host)
        assertEquals("STALE_BASIS", stale.data?.reason)
        assertNull(host.border)
    }

    @Test fun policyDeniedShowsOnlyFixedSentenceAndDeniedOverlayIsError() {
        val host = Host()
        val denied = run(at(100).copy(policyDenied = true), host)
        assertEquals("policy_denied", denied.outcome)
        assertEquals("되돌리기 어려운 동작이에요. 천천히 확인하세요", host.banner)

        val noPermission = ParentGuideFlow.run(at(3_000), diagnostic, host, false)
        assertEquals(NodeStatus.ERROR, noPermission.status)
        assertNull(noPermission.data)
        assertEquals("OVERLAY_DENIED", noPermission.diagnostics.terminal_error?.code)
        assertNull(host.border)
    }

    @Test fun b06_3_newInstructionDropsOldTimerAndDisplay() {
        val clock = GateClock()
        clock.applyMode("help", HelpMode.CALL_CHILD_LED, 1)
        clock.acceptInstruction("a", 1, 0)
        val host = Host()
        val stale = at(3_000).copy(latestBasis = basis.copy(instruction_revision = 2))
        val result = ParentGuideFlow.run(stale, diagnostic, host, true, clock)
        assertEquals("STALE_BASIS", result.data?.reason)
        assertNull(host.border)
        assertNull(clock.t0_mono_ms)
    }
}
