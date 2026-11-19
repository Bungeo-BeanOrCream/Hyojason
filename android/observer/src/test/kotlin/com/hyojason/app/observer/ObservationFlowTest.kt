package com.hyojason.app.observer

import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.detection.CaptureFrame
import com.hyojason.app.observer.detection.DemoProfiles
import com.hyojason.app.observer.detection.DetectionInput
import com.hyojason.app.observer.detection.FeatureKey
import com.hyojason.app.observer.detection.FeatureKind
import com.hyojason.app.observer.detection.FeatureScan
import com.hyojason.app.observer.detection.FramePixels
import com.hyojason.app.observer.detection.FrameFeature
import com.hyojason.app.observer.detection.FrameSafety
import com.hyojason.app.observer.detection.ScreenDetection
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** B-03 end to end on synthetic frames (FIXTURE): N-08 -> N-09 -> N-10. */
class ObservationFlowTest {
    private val profile = DemoProfiles.fixture
    private val basis = Basis("pair", "call", "help", 1, 1, null, 1, 1, "epoch", 0, null)
    private val diagnostic = Diagnostic("trace", "run", "B-03", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null)
    private val bounds = LocalRect(0, 0, 20, 20)
    private val title = LocalRect(1, 1, 7, 3)
    private val anchor = LocalRect(1, 5, 7, 7)
    private val target = LocalRect(1, 9, 7, 11)
    private val scrolled = LocalRect(1, 12, 7, 14)

    private var features: List<Pair<String, LocalRect>> = emptyList()
    private var scanOk = true
    private var scanCalls = 0

    private fun flow(epoch: String = "epoch", reuseIdenticalScans: Boolean = false) = ObservationFlow(
        epoch, ScreenDetection(listOf(profile)), profile.id, profile.revision, profile.profile_version,
        scan = { _: DetectionInput ->
            scanCalls++
            listOf(FeatureScan(FeatureKind.TEXT, scanOk, if (scanOk) features.map { (v, r) ->
                FrameFeature(FeatureKey(FeatureKind.TEXT, v), r)
            } else emptyList()))
        },
        reuseIdenticalScans = reuseIdenticalScans,
    )

    private fun settings(display: LocalRect = target) =
        listOf("설정" to title, "네트워크 및 인터넷" to anchor, "디스플레이" to display)

    private fun frame(
        time: Long, epoch: String = "epoch", safety: FrameSafety = FrameSafety.ALLOWED,
        masks: List<LocalRect> = emptyList(), argb: IntArray = IntArray(400) { 0xffffffff.toInt() },
    ) = CaptureFrame(epoch, FramePixels(20, 20, argb), time, bounds, bounds, 0, masks, safety)

    @Test fun b03_1_scrollChangesLayoutOnlyAndKeepsScreenRevision() {
        val flow = flow()
        features = settings()
        assertNull(flow.process(frame(100), diagnostic).stabilization?.screenToPublish)
        val stable = flow.process(frame(350), diagnostic).stabilization!!
        assertEquals("screen_changed", stable.result?.outcome)
        assertEquals(1L, stable.screenToPublish?.screen_revision)
        assertEquals(setOf("settings.display"), stable.screenToPublish?.target_ids)

        features = settings(scrolled)
        val moved = flow.process(frame(600), diagnostic).stabilization!!
        assertEquals("layout_changed", moved.result?.outcome)
        assertEquals(1L, moved.local?.screen?.screen_revision)
        assertEquals(scrolled, moved.local?.targets?.single()?.rect)
        assertEquals(2L, moved.local?.layout_revision)
        assertNull(moved.screenToPublish)
    }

    @Test fun b03_2_oldEpochFrameIsStaleAndLeavesStateAndPixelsClean() {
        val flow = flow()
        features = settings()
        flow.process(frame(100), diagnostic)
        val buffer = IntArray(400) { 0xffffffff.toInt() }
        val outcome = flow.process(frame(200, epoch = "old", argb = buffer), diagnostic)
        assertEquals("STALE_BASIS", outcome.prepared.diagnostics.terminal_error?.code)
        assertNull(outcome.stabilization)
        assertNull(outcome.detection)
        assertEquals(1, scanCalls)
        assertTrue(buffer.all { it == 0 })
        assertEquals("screen_changed", flow.process(frame(350), diagnostic).stabilization?.result?.outcome)
    }

    @Test fun b03_3_protectedAndScannerFailureBecomeGapsWithNoTargets() {
        val flow = flow()
        features = settings()
        flow.process(frame(100), diagnostic)
        flow.process(frame(350), diagnostic)
        val protectedFrame = flow.process(frame(500, safety = FrameSafety.PROTECTED), diagnostic)
        assertEquals(ObservationHealth.PROTECTED, protectedFrame.stabilization?.screenToPublish?.observation_health)
        assertEquals("protected", protectedFrame.stabilization?.screenToPublish?.screen_kind)
        assertEquals(emptySet<String>(), protectedFrame.stabilization?.screenToPublish?.target_ids)
        assertNull(protectedFrame.stabilization?.local)
        assertNull(protectedFrame.detection)

        scanOk = false
        val failed = flow.process(frame(650), diagnostic)
        assertEquals(ObservationHealth.GAP, failed.stabilization?.screenToPublish?.observation_health)
        assertEquals("observation_gap", failed.stabilization?.result?.outcome)
        assertNull(failed.stabilization?.local)
    }

    @Test fun b03_4_overlayMaskIsNotUsedAsEvidence() {
        val flow = flow()
        features = settings()
        val masked = listOf(LocalRect(0, 8, 20, 12))
        flow.process(frame(100, masks = masked), diagnostic)
        val outcome = flow.process(frame(350, masks = masked), diagnostic)
        assertTrue(outcome.detection?.data?.targets.orEmpty().isEmpty())
        assertEquals(emptySet<String>(), outcome.stabilization?.local?.screen?.target_ids)
    }

    @Test fun b03_1_identicalFreshFrameReusesScanUntilPixelsChange() {
        val flow = flow(reuseIdenticalScans = true)
        features = settings()
        flow.process(frame(100), diagnostic)
        val stable = flow.process(frame(350), diagnostic).stabilization!!
        assertEquals(1, scanCalls)
        assertEquals(350L, stable.local?.captured_mono_ms)
        assertEquals(target, stable.local?.targets?.single()?.rect)

        val changedPixels = IntArray(400) { 0xffffffff.toInt() }
        changedPixels[0] = 0xffeeeeee.toInt()
        features = settings(scrolled)
        val moved = flow.process(frame(600, argb = changedPixels), diagnostic).stabilization!!
        assertEquals(2, scanCalls)
        assertEquals(scrolled, moved.local?.targets?.single()?.rect)
    }
}
