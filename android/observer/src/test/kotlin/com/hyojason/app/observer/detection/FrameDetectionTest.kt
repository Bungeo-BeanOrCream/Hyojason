package com.hyojason.app.observer.detection

import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.Source
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
    fun n09_1_knownSettingsAndClockFramesFindProfileTargets() = withFrame { input ->
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
        assertEquals(setOf("alarm_card_active"), clock.data?.verified_signals)
    }

    @Test
    fun n09_2_emptyAndAmbiguousTargetsAreNotAConfirmedAction() = withFrame { input ->
        val empty = engine.detect(input, settings(display = false), diagnostic)
        assertEquals("no_targets", empty.outcome)
        assertEquals(emptyList<Any>(), empty.data?.targets)
        val duplicate = scan(FeatureKind.TEXT, "설정" to title, "네트워크 및 인터넷" to anchor,
            "디스플레이" to target, "디스플레이" to LocalRect(11, 9, 17, 11))
        assertEquals("no_targets", engine.detect(input, listOf(duplicate), diagnostic).outcome)
    }

    @Test
    fun n09_3_n09_5_failedOcrPreservesIndependentIconAndDiagnosticBasis() = withFrame { input ->
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
    fun n09_4_incorrectProfileAllFailedAndFixtureInLiveAreErrors() = withFrame { input ->
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
    fun n09_2_profileVersionMustMatchBothAppAndAvdToEmitTargetsOrSignals() = withFrame { input ->
        val scans = listOf(
            scan(FeatureKind.ICON, "clock_alarm_list" to title, "add_alarm" to target),
            scan(FeatureKind.STATE, "active_alarm_card" to anchor),
        )
        val expected = profile.profile_version
        for (version in listOf(null, expected.copy(appVersionName = "other"), expected.copy(avdImage = "other"))) {
            val actual = input.copy(profile_version = version)
            val result = engine.detect(actual, if (version == null) emptyList() else scans, diagnostic)
            assertEquals(NodeStatus.SUCCESS, result.status)
            assertEquals("no_targets", result.outcome)
            val data = requireNotNull(result.data)
            assertEquals("unknown", data.screen_kind)
            assertTrue(data.targets.isEmpty())
            assertTrue(data.verified_signals.isEmpty())
            assertEquals(ObservationHealth.GAP, engine.sample(actual, data, "epoch").health)
        }
    }

    @Test
    fun n09_1_n09_2_smsSendRequiresApprovedRecipientAndDraft() = withFrame { input ->
        val screen = scan(FeatureKind.ICON, "sms_conversation" to title)
        val sendLabel = scan(FeatureKind.TEXT, "보내기" to target)
        val recipientOnly = scan(FeatureKind.STATE, "demo_recipient_verified" to anchor)
        val missingDraft = engine.detect(input, listOf(screen, sendLabel, recipientOnly), diagnostic)
        assertEquals("sms_conversation", missingDraft.data?.screen_kind)
        assertEquals("no_targets", missingDraft.outcome)

        val approved = scan(FeatureKind.STATE, "demo_recipient_verified" to anchor, "demo_draft_verified" to belowTarget())
        val result = engine.detect(input, listOf(screen, sendLabel, approved), diagnostic)
        assertEquals("sms.send", result.data?.targets?.single()?.target_id)
        assertTrue(result.data?.verified_signals?.isEmpty() == true)
    }

    @Test
    fun n09_1_n09_2_repeatedLabelsUseRegisteredRowIdsAcrossScroll() = withFrame { input ->
        val below = LocalRect(1, 14, 7, 16)
        val upperRow = LocalRect(0, 8, 20, 12)
        val lowerRow = LocalRect(0, 13, 20, 17)
        val visible = engine.detect(input, listOf(
            scan(FeatureKind.ICON, "clock_alarm_list" to title, "alarm_fixture_row_a" to upperRow,
                "alarm_fixture_row_b" to lowerRow),
            scan(FeatureKind.TEXT, "알람 켜기" to below, "알람 켜기" to target),
        ), diagnostic)
        assertEquals(listOf("clock.alarm_toggle#1", "clock.alarm_toggle#2"),
            visible.data?.targets?.map { it.target_id })
        assertEquals(listOf(target, below), visible.data?.targets?.map { it.rect })

        val scrolled = engine.detect(input, listOf(
            scan(FeatureKind.ICON, "clock_alarm_list" to title, "alarm_fixture_row_b" to upperRow),
            scan(FeatureKind.TEXT, "알람 켜기" to target),
        ), diagnostic)
        assertEquals("clock.alarm_toggle#2", scrolled.data?.targets?.single()?.target_id)
        assertEquals(target, scrolled.data?.targets?.single()?.rect)

        val ambiguous = engine.detect(input, listOf(
            scan(FeatureKind.ICON, "clock_alarm_list" to title),
            scan(FeatureKind.TEXT, "알람 켜기" to target),
        ), diagnostic)
        assertEquals("no_targets", ambiguous.outcome)
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
    fun b03_4_masksRemovePixelsTargetsAndArrivalEvidence() = withFrame(listOf(target, anchor)) { input ->
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
        requireNotNull(result.data).use { block(DetectionInput(it, profile.id, profile.revision, null, profile.profile_version)) }
    }

    private fun rawFrame(argb: IntArray) = CaptureFrame(
        "epoch", FramePixels(20, 20, argb), 1, bounds, bounds, 0, emptyList(), FrameSafety.ALLOWED,
    )

    private fun pixels() = IntArray(20 * 20) { 0xffffffff.toInt() }

    private fun belowTarget() = LocalRect(1, 14, 7, 16)

    private fun settings(display: Boolean = true): List<FeatureScan> {
        val titleFeatures = listOf("설정" to title, "네트워크 및 인터넷" to anchor)
        val features = if (display) titleFeatures + ("디스플레이" to target) else titleFeatures
        return listOf(scan(FeatureKind.TEXT, *features.toTypedArray()))
    }

    private fun scan(kind: FeatureKind, vararg features: Pair<String, LocalRect>) =
        FeatureScan(kind, true, features.map { (value, rect) -> FrameFeature(FeatureKey(kind, value), rect) })
}
