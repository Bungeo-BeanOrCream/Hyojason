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
    private val settings = "settings_home"
    private val displaySettings = "settings_display"

    @Test
    fun B03_1_bothVisibleIdsAndCoordinatesCanChangeWithoutSemanticRevision() {
        assertNull(observe(100).screenToPublish)
        assertNull(observe(100).screenToPublish)
        val stable = observe(350)
        assertEquals("screen_changed", stable.result?.outcome)
        assertEquals(1L, stable.screenToPublish?.screen_revision)
        val scrolled = observe(600, targets = listOf(timeout), frameLayout = 1)
        assertEquals("layout_changed", scrolled.result?.outcome)
        assertEquals(stable.local?.screen?.screen_revision, scrolled.local?.screen?.screen_revision)
        assertEquals(2L, scrolled.local?.layout_revision)
        assertNull(scrolled.fromScreenRevision)
        assertEquals(setOf(timeout.target_id), scrolled.local?.screen?.target_ids)
        assertTrue(scrolled.locationInvalidated)
        assertEquals(setOf(timeout.target_id), scrolled.screenToPublish?.target_ids)
        assertFalse(observe(850, targets = listOf(timeout), frameLayout = 1).locationInvalidated)
        val moved = timeout.copy(rect = LocalRect(1, 21, 30, 61))
        val coordinateOnly = observe(1_100, targets = listOf(moved), frameLayout = 1)
        assertEquals("layout_changed", coordinateOnly.result?.outcome)
        assertEquals(1L, coordinateOnly.local?.screen?.screen_revision)
        assertNull(coordinateOnly.screenToPublish)
    }

    @Test
    fun N10_1_candidateHidesImmediatelyAndConfirmedScreenKindAdvancesRevision() {
        observe(100)
        observe(350)
        val pending = observe(600, kind = displaySettings)
        assertNull(pending.local)
        assertTrue(pending.locationInvalidated)
        assertTrue(pending.verdictPending)
        val changed = observe(850, kind = displaySettings)
        assertEquals("screen_changed", changed.result?.outcome)
        assertEquals(2L, changed.screenToPublish?.screen_revision)
        assertEquals(1L, changed.fromScreenRevision)
        assertEquals(basis, changed.result?.diagnostics?.basis)
        assertEquals("run-fixture", changed.result?.diagnostics?.run_id)
    }

    @Test
    fun N10_3_protectedGapDiscardsTargetsAndBreaksConsecutiveness() {
        observe(100)
        val gap = observe(350, health = ObservationHealth.PROTECTED)
        assertEquals("observation_gap", gap.result?.outcome)
        assertEquals("protected", gap.screenToPublish?.screen_kind)
        assertEquals(emptySet<String>(), gap.screenToPublish?.target_ids)
        assertEquals(emptySet<String>(), gap.screenToPublish?.evidence_codes)
        assertNull(gap.local)
        assertTrue(gap.locationInvalidated)
        assertNull(observe(600).screenToPublish)
        assertEquals(1L, observe(850).screenToPublish?.screen_revision)
    }

    @Test
    fun N10_4_staleEpochDoesNotAdvanceOrOverwriteCurrentState() {
        observe(100)
        val stale = observe(850, epoch = "old", kind = displaySettings)
        assertNull(stale.result?.data)
        assertEquals("STALE_BASIS", stale.result?.diagnostics?.terminal_error?.code)
        assertNull(stale.screenToPublish)
        assertEquals("settings_home", observe(350).screenToPublish?.screen_kind)
    }

    @Test
    fun N10_1_sameKindEvidenceChangeRequiresTwoFramesAndOnlyAdvancesScreenRevision() {
        observe(100, kind = displaySettings)
        val previous = requireNotNull(observe(350, kind = displaySettings).local)
        val selected = setOf("timeout_10m_selected")
        val candidate = observe(600, kind = displaySettings, evidence = selected)
        assertTrue(candidate.verdictPending)
        assertNull(candidate.screenToPublish)
        val confirmed = observe(850, kind = displaySettings, evidence = selected)
        assertEquals("screen_changed", confirmed.result?.outcome)
        assertEquals(previous.screen.screen_revision + 1, confirmed.screenToPublish?.screen_revision)
        assertEquals(selected, confirmed.screenToPublish?.evidence_codes)
        assertEquals(previous.screen.screen_revision, confirmed.fromScreenRevision)
        assertEquals(previous.layout_revision, confirmed.local?.layout_revision)
        assertTrue(confirmed.locationInvalidated)
    }

    @Test
    fun N10_3_gapDoesNotAdvanceRevisionsAndRecoveryStartsANewStableSegment() {
        observe(100)
        val prior = requireNotNull(observe(350).local)
        for ((time, health) in listOf(600L to ObservationHealth.GAP, 850L to ObservationHealth.PROTECTED)) {
            val gap = observe(time, health = health)
            assertEquals(prior.screen.screen_revision, gap.result?.data?.screen_revision)
            assertEquals(prior.layout_revision, gap.result?.data?.layout_revision)
        }
        assertNull(observe(1_100).screenToPublish)
        val recovered = observe(1_350)
        assertEquals(prior.screen.screen_revision + 1, recovered.screenToPublish?.screen_revision)
        assertFalse(recovered.verdictPending)
        assertNull(recovered.fromScreenRevision)
    }

    @Test
    fun B03_2_newEpochUsesPreviousScreenRevisionWithoutReset() {
        observe(100)
        val prior = requireNotNull(observe(350).local)
        val next = ScreenStabilizer("next", prior.screen.screen_revision, prior.layout_revision)
        val nextDiagnostic = diagnostic.copy(basis = basis.copy(projection_epoch = "next"))
        val sample = ScreenSample("next", 600, settings, 0, listOf(display), emptySet(), ObservationHealth.STABLE)
        assertNull(next.observe(sample, nextDiagnostic).screenToPublish)
        val confirmed = next.observe(sample.copy(capturedMonoMs = 850), nextDiagnostic)
        assertEquals(prior.screen.screen_revision + 1, confirmed.screenToPublish?.screen_revision)
        assertEquals("next", confirmed.screenToPublish?.projection_epoch)
    }

    private fun observe(
        time: Long,
        kind: String = settings,
        targets: List<LocalTarget> = listOf(display),
        frameLayout: Long = 0,
        health: ObservationHealth = ObservationHealth.STABLE,
        epoch: String = "current",
        evidence: Set<String> = emptySet(),
    ): Stabilization = stabilizer.observe(
        ScreenSample(epoch, time, kind, frameLayout, targets, evidence, health),
        diagnostic,
    )
}
