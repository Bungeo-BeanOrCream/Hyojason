package com.hyojason.app.observer

import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.Source
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenStabilizerTest {
    private val stabilizer = ScreenStabilizer("current")
    private val basis = Basis("pair", "call", "help", 5, 1, null, 1, 1, "current", 0, null)
    private val diagnostic = Diagnostic(
        "trace-fixture", "run-fixture", "N-10", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null,
    )
    private val display = LocalTarget("settings.display", LocalRect(1, 2, 30, 40), setOf("PROFILE_MATCH"))
    private val timeout = LocalTarget("settings.timeout", LocalRect(1, 20, 30, 60), setOf("PROFILE_MATCH"))
    private val settings = SemanticScreen("settings_home", setOf(display.target_id, timeout.target_id))
    private val displaySettings = SemanticScreen("settings_display", setOf(timeout.target_id))

    @Test
    fun confirmsOnlyTwoConsecutiveDistinctSamplesThenScrollChangesLayoutOnly() {
        assertNull(observe(100).screenToPublish)
        assertNull(observe(100).screenToPublish)
        val stable = observe(350)
        assertEquals("screen_changed", stable.result?.outcome)
        assertEquals(1L, stable.screenToPublish?.screen_revision)
        val scrolled = observe(600, targets = listOf(timeout), frameLayout = 1)
        assertEquals("layout_changed", scrolled.result?.outcome)
        assertEquals(stable.local?.screen?.screen_revision, scrolled.local?.screen?.screen_revision)
        assertEquals(2L, scrolled.local?.layout_revision)
        assertEquals(setOf(timeout.target_id), scrolled.local?.screen?.target_ids)
        assertTrue(scrolled.locationInvalidated)
        assertNull(scrolled.screenToPublish)
        assertFalse(observe(850, targets = listOf(timeout), frameLayout = 1).locationInvalidated)
    }

    @Test
    fun candidateHidesImmediatelyAndConfirmedMeaningChangeAdvancesRevision() {
        observe(100)
        observe(350)
        val pending = observe(600, semantic = displaySettings)
        assertNull(pending.local)
        assertTrue(pending.locationInvalidated)
        assertTrue(pending.verdictPending)
        val changed = observe(850, semantic = displaySettings)
        assertEquals("screen_changed", changed.result?.outcome)
        assertEquals(2L, changed.screenToPublish?.screen_revision)
        assertEquals(basis, changed.result?.diagnostics?.basis)
        assertEquals("run-fixture", changed.result?.diagnostics?.run_id)
    }

    @Test
    fun protectedGapDiscardsTargetsAndBreaksConsecutiveness() {
        observe(100)
        val gap = observe(350, health = ObservationHealth.PROTECTED)
        assertEquals("observation_gap", gap.result?.outcome)
        assertEquals("unknown", gap.screenToPublish?.screen_kind)
        assertEquals(emptySet<String>(), gap.screenToPublish?.target_ids)
        assertEquals(emptySet<String>(), gap.screenToPublish?.evidence_codes)
        assertNull(gap.local)
        assertTrue(gap.locationInvalidated)
        assertNull(observe(600).screenToPublish)
        assertEquals(1L, observe(850).screenToPublish?.screen_revision)
    }

    @Test
    fun staleEpochDoesNotAdvanceOrOverwriteCurrentState() {
        observe(100)
        val stale = observe(850, epoch = "old", semantic = displaySettings)
        assertNull(stale.result?.data)
        assertEquals("STALE_BASIS", stale.result?.diagnostics?.terminal_error?.code)
        assertNull(stale.screenToPublish)
        assertEquals("settings_home", observe(350).screenToPublish?.screen_kind)
    }

    private fun observe(
        time: Long,
        semantic: SemanticScreen = settings,
        targets: List<LocalTarget> = listOf(display),
        frameLayout: Long = 0,
        health: ObservationHealth = ObservationHealth.STABLE,
        epoch: String = "current",
    ): Stabilization = stabilizer.observe(
        ScreenSample(epoch, time, semantic, frameLayout, targets, setOf("PROFILE_MATCH"), health),
        diagnostic,
    )
}
