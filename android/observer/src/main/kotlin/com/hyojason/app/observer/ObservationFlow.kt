package com.hyojason.app.observer

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.observer.detection.CaptureFrame
import com.hyojason.app.observer.detection.DetectionData
import com.hyojason.app.observer.detection.DetectionInput
import com.hyojason.app.observer.detection.FeatureScan
import com.hyojason.app.observer.detection.FrameHealth
import com.hyojason.app.observer.detection.FramePreparation
import com.hyojason.app.observer.detection.LocalFrame
import com.hyojason.app.observer.detection.PROTECTED_SCREEN_KIND
import com.hyojason.app.observer.detection.ProfileVersion
import com.hyojason.app.observer.detection.ScreenDetection
import com.hyojason.app.observer.detection.UNKNOWN_SCREEN_KIND

/** What one raw frame produced. [stabilization] is null when N-08 rejected the frame (state untouched). */
data class ObservationOutcome(
    val prepared: NodeResult<LocalFrame>,
    val detection: NodeResult<DetectionData>?,
    val stabilization: Stabilization?,
)

/**
 * B-03 on one projection epoch: N-08 normalize -> N-09 detect -> N-10 stabilize.
 * [scan] runs the feature scanners (e.g. KoreanTextScan) on the normalized frame and must return
 * before this call returns; the Android wiring blocks a worker thread for the ML Kit callback.
 * Not thread-safe: call from one serialized loop, like the stabilizer it owns.
 * Pixels never leave this call: the normalized frame is closed on every path.
 */
class ObservationFlow(
    private val projectionEpoch: String,
    private val detection: ScreenDetection,
    private val profileId: String,
    private val profileRevision: Int,
    private val profileVersion: ProfileVersion?,
    private val scan: (DetectionInput) -> List<FeatureScan>,
    previousScreenRevision: Long = 0,
    previousLayoutRevision: Long = 0,
) {
    private val preparation = FramePreparation(projectionEpoch)
    private val stabilizer = ScreenStabilizer(projectionEpoch, previousScreenRevision, previousLayoutRevision)

    fun process(frame: CaptureFrame, diagnostic: Diagnostic, screenHint: String? = null): ObservationOutcome {
        val prepared = preparation.prepare(frame, diagnostic)
        val local = prepared.data ?: return ObservationOutcome(prepared, null, null)
        return local.use {
            if (it.frame_health != FrameHealth.READY) {
                val protectedScreen = it.frame_health == FrameHealth.PROTECTED
                val sample = ScreenSample(
                    projectionEpoch, it.captured_mono_ms,
                    if (protectedScreen) PROTECTED_SCREEN_KIND else UNKNOWN_SCREEN_KIND,
                    it.layout_revision, emptyList(), emptySet(),
                    if (protectedScreen) ObservationHealth.PROTECTED else ObservationHealth.GAP,
                )
                return@use ObservationOutcome(prepared, null, stabilizer.observe(sample, diagnostic))
            }
            val input = DetectionInput(it, profileId, profileRevision, screenHint, profileVersion)
            val detected = detection.detect(input, scan(input), diagnostic)
            val sample = detected.data?.let { data -> detection.sample(input, data, projectionEpoch) }
                ?: ScreenSample(
                    projectionEpoch, it.captured_mono_ms, UNKNOWN_SCREEN_KIND, it.layout_revision,
                    emptyList(), emptySet(), ObservationHealth.GAP,
                )
            ObservationOutcome(prepared, detected, stabilizer.observe(sample, diagnostic))
        }
    }
}
