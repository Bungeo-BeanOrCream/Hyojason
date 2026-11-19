package com.hyojason.app.observer.projection

import com.hyojason.app.observer.contract.Basis
import com.hyojason.app.observer.contract.Diagnostic
import com.hyojason.app.observer.contract.NodeResult
import com.hyojason.app.observer.contract.NodeStatus
import com.hyojason.app.observer.contract.Source
import com.hyojason.app.observer.detection.FramePixels
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProjectionSessionTest {
    private val basis = Basis("pair", "call", "help", 2, 1, null, 1, 1, null, null, null)
    private val diagnostic = Diagnostic("trace", "run", "N-07", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null)
    private var starts = 0
    private var stops = 0
    private var cleared = 0
    private var epoch = 0
    private val systemCallbacks = mutableListOf<() -> Unit>()
    private val session = ProjectionSession(
        ProjectionBackend<String> { _, _, stop ->
            starts++
            systemCallbacks += stop
            AutoCloseable { stops++ }
        },
        onInvalidated = { cleared++ },
        newEpoch = { "epoch-${++epoch}" },
    )

    @Test
    fun newApprovalCreatesFreshEpochAndClosesThePreviousSource() {
        val first = open()
        val frame = intArrayOf(1)
        val firstSource = requireNotNull(first.frame_source)
        firstSource.publish(CapturedPixels(firstSource.epoch, 1, FramePixels(1, 1, frame)))
        val second = open()
        assertNotEquals(first.projection_epoch, second.projection_epoch)
        assertEquals(2, starts)
        assertEquals(1, stops)
        assertNull(firstSource.take())
        assertEquals(0, frame[0])
        systemCallbacks.first().invoke()
        assertEquals(1, stops)
    }

    @Test
    fun systemStopClearsQueuedFramesAndReportsStopped() {
        var result: NodeResult<ProjectionData>? = null
        val started = session.open("help", ProjectionApproval.approved("consent"), diagnostic) { result = it }
        val source = requireNotNull(started.data?.frame_source)
        val pixels = intArrayOf(7)
        source.publish(CapturedPixels(source.epoch, 1, FramePixels(1, 1, pixels)))
        systemCallbacks.single().invoke()
        assertEquals("capture_stopped", result?.outcome)
        assertEquals(CaptureState.STOPPED, result?.data?.capture_state)
        assertEquals(source.epoch, result?.diagnostics?.basis?.projection_epoch)
        assertNotEquals(diagnostic.run_id, result?.diagnostics?.run_id)
        assertEquals(1, stops)
        assertEquals(1, cleared)
        assertNull(source.take())
        assertEquals(0, pixels[0])
    }

    @Test
    fun denialDoesNotStartAndDiscardsCurrentDisplay() {
        val result = session.open("help", ProjectionApproval.denied(), diagnostic)
        assertEquals("capture_denied", result.outcome)
        assertEquals(CaptureState.WAITING, result.data?.capture_state)
        assertNull(result.data?.frame_source)
        assertEquals(0, starts)
        assertEquals(1, cleared)
    }

    @Test
    fun repeatedApprovalAndBackendFailureAreErrors() {
        val approval = ProjectionApproval.approved("one-shot")
        assertEquals("capture_started", session.open("help", approval, diagnostic).outcome)
        val repeated = session.open("help", approval, diagnostic)
        assertEquals(NodeStatus.ERROR, repeated.status)
        assertNull(repeated.data)
        assertEquals("DUPLICATE", repeated.diagnostics.terminal_error?.code)
        assertEquals(basis, repeated.diagnostics.basis)
        assertEquals(diagnostic.run_id, repeated.diagnostics.run_id)
        assertEquals(1, starts)
        val failing = ProjectionSession(
            ProjectionBackend<String> { _, _, _ -> throw IllegalStateException("fixture backend") },
            onInvalidated = { cleared++ },
        )
        val failed = failing.open("help", ProjectionApproval.approved("different"), diagnostic)
        assertEquals("DEPENDENCY_UNAVAILABLE", failed.diagnostics.terminal_error?.code)
        assertNull(failed.data)
        assertEquals(1, cleared)
    }

    @Test
    fun newestFrameWinsAndOtherEpochIsWiped() {
        val source = requireNotNull(open().frame_source)
        val first = intArrayOf(1)
        val second = intArrayOf(2)
        val stale = intArrayOf(3)
        source.publish(CapturedPixels(source.epoch, 1, FramePixels(1, 1, first)))
        source.publish(CapturedPixels(source.epoch, 2, FramePixels(1, 1, second)))
        source.publish(CapturedPixels("stale", 3, FramePixels(1, 1, stale)))
        assertEquals(0, first[0])
        assertEquals(0, stale[0])
        requireNotNull(source.take()).use { assertEquals(2L, it.capturedMonoMs) }
        assertEquals(0, second[0])
        assertTrue(source.take() == null)
    }

    private fun open(): ProjectionData =
        requireNotNull(session.open("help", ProjectionApproval.approved("new-consent"), diagnostic).data)
}
