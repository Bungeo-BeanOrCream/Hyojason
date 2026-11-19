package com.hyojason.app.observer.detection

import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.contract.Basis
import com.hyojason.app.observer.contract.Diagnostic
import com.hyojason.app.observer.contract.NodeStatus
import com.hyojason.app.observer.contract.Source
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FrameDetectionTest {
    private val profile = DemoProfiles.fixture
    private val engine = ScreenDetection(listOf(profile))
    private val prepare = FramePreparation("epoch")
    private val basis = Basis("pair", "call", "help", 1, 1, null, 1, 1, "epoch", 1, null)
    private val diagnostic = Diagnostic("trace", "run", "N-09", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null)
    private val bounds = LocalRect(0, 0, 20, 20)
    private val title = LocalRect(1, 1, 7, 3)
    private val anchor = LocalRect(1, 5, 7, 7)
    private val target = LocalRect(1, 9, 7, 11)

    @Test
    fun knownSettingsAndClockFramesFindProfileTargets() = withFrame { input ->
        val result = engine.detect(input, settings(), diagnostic)
        assertEquals("targets_found", result.outcome)
        assertEquals("settings_home", result.data?.screen_kind)
        assertEquals("settings.display", result.data?.targets?.single()?.target_id)
        assertEquals(target, result.data?.targets?.single()?.rect)
        val clock = engine.detect(input, listOf(
            scan(FeatureKind.ICON, "clock_alarm_list" to title, "add_alarm" to target),
            scan(FeatureKind.STATE, "active_alarm_card" to anchor),
        ), diagnostic)
        assertEquals("clock.add_alarm", clock.data?.targets?.single()?.target_id)
        assertEquals(setOf("ACTIVE_ALARM_CARD"), clock.data?.verified_signals)
    }

    @Test
    fun emptyAndAmbiguousTargetsAreNotAConfirmedAction() = withFrame { input ->
        val empty = engine.detect(input, settings(display = false), diagnostic)
        assertEquals("no_targets", empty.outcome)
        assertEquals(emptyList<Any>(), empty.data?.targets)
        val duplicate = scan(FeatureKind.TEXT, "설정" to title, "네트워크 및 인터넷" to anchor,
            "디스플레이" to target, "디스플레이" to LocalRect(11, 9, 17, 11))
        assertEquals("no_targets", engine.detect(input, listOf(duplicate), diagnostic).outcome)
    }

    @Test
    fun failedOcrPreservesIndependentIconAndDiagnosticBasis() = withFrame { input ->
        val result = engine.detect(input, listOf(
            FeatureScan(FeatureKind.TEXT, false, emptyList()),
            scan(FeatureKind.ICON, "clock_alarm_list" to title, "add_alarm" to target),
        ), diagnostic)
        assertEquals(NodeStatus.PARTIAL, result.status)
        assertEquals("observation_partial", result.outcome)
        assertEquals("clock.add_alarm", result.data?.targets?.single()?.target_id)
        assertEquals(basis, result.diagnostics.basis)
        assertEquals("run", result.diagnostics.run_id)
        assertNull(result.diagnostics.terminal_error)
        assertEquals("DEPENDENCY_UNAVAILABLE", result.diagnostics.steps.getValue("detect_targets").code)
    }

    @Test
    fun incorrectProfileAllFailedAndFixtureInLiveAreErrors() = withFrame { input ->
        val failures = listOf(
            engine.detect(input.copy(profile_revision = 2), settings(), diagnostic),
            engine.detect(input, listOf(FeatureScan(FeatureKind.TEXT, false, emptyList())), diagnostic),
            engine.detect(input, settings(), diagnostic.copy(source = Source.LIVE)),
        )
        for (result in failures) {
            assertEquals(NodeStatus.ERROR, result.status)
            assertNull(result.outcome)
            assertNull(result.data)
        }
    }

    @Test
    fun protectedAndUncertainFramesNeverExposePixels() {
        val raw = pixels()
        val protected = prepare.prepare(rawFrame(raw).copy(safety = FrameSafety.PROTECTED), diagnostic)
        assertEquals("protected_screen", protected.outcome)
        assertNull(protected.data?.frame_ref)
        assertTrue(raw.all { it == 0 })
        val uncertain = prepare.prepare(rawFrame(pixels()).copy(captured_mono_ms = 2, viewport = null), diagnostic)
        assertEquals("geometry_uncertain", uncertain.outcome)
        assertNull(uncertain.data?.frame_ref)
    }

    @Test
    fun masksRemovePixelsTargetsAndArrivalEvidence() = withFrame(listOf(target, anchor)) { input ->
        val frame = requireNotNull(input.local_frame.frame_ref)
        val normalized = frame.pixels.copyArgb()
        assertEquals(0xff000000.toInt(), normalized[target.top * 20 + target.left])
        assertEquals(0xffffffff.toInt(), normalized[0])
        val result = engine.detect(input, listOf(
            scan(FeatureKind.ICON, "clock_alarm_list" to title, "add_alarm" to target),
            scan(FeatureKind.STATE, "active_alarm_card" to anchor),
        ), diagnostic)
        assertEquals("no_targets", result.outcome)
        assertTrue(result.data?.verified_signals?.isEmpty() == true)
    }

    @Test
    fun staleEpochDoesNotChangeLayoutAndViewportChangesIncrementIt() {
        val stale = prepare.prepare(rawFrame(pixels()).copy(projection_epoch = "old"), diagnostic)
        assertEquals("STALE_BASIS", stale.diagnostics.terminal_error?.code)
        requireNotNull(prepare.prepare(rawFrame(pixels()), diagnostic).data).use {
            assertEquals(1L, it.layout_revision)
        }
        val inset = rawFrame(pixels()).copy(captured_mono_ms = 2, viewport = LocalRect(0, 1, 20, 20))
        requireNotNull(prepare.prepare(inset, diagnostic).data).use {
            assertEquals(2L, it.layout_revision)
            assertEquals(19, it.frame_ref?.pixels?.height)
        }
    }

    private fun withFrame(masks: List<LocalRect> = emptyList(), block: (DetectionInput) -> Unit) {
        val buffer = pixels()
        val result = prepare.prepare(rawFrame(buffer).copy(overlay_masks = masks), diagnostic)
        assertEquals("frame_ready", result.outcome)
        assertTrue(buffer.all { it == 0 })
        requireNotNull(result.data).use { block(DetectionInput(it, profile.id, profile.revision, null)) }
    }

    private fun rawFrame(argb: IntArray) = CaptureFrame(
        "epoch", FramePixels(20, 20, argb), 1, bounds, bounds, 0, emptyList(), FrameSafety.ALLOWED,
    )

    private fun pixels() = IntArray(20 * 20) { 0xffffffff.toInt() }

    private fun settings(display: Boolean = true): List<FeatureScan> {
        val titleFeatures = listOf("설정" to title, "네트워크 및 인터넷" to anchor)
        val features = if (display) titleFeatures + ("디스플레이" to target) else titleFeatures
        return listOf(scan(FeatureKind.TEXT, *features.toTypedArray()))
    }

    private fun scan(kind: FeatureKind, vararg features: Pair<String, LocalRect>) =
        FeatureScan(kind, true, features.map { (value, rect) -> FrameFeature(FeatureKey(kind, value), rect) })
}
