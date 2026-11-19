package com.hyojason.app.observer.projection

import com.hyojason.app.observer.contract.Diagnostic
import com.hyojason.app.observer.contract.NodeResult
import com.hyojason.app.observer.contract.NodeStatus
import com.hyojason.app.observer.detection.FramePixels
import com.hyojason.app.observer.detection.failure
import com.hyojason.app.observer.detection.recorded
import java.util.UUID

class ProjectionApproval<T : Any> private constructor(
    internal val approved: Boolean,
    private var token: T?,
) {
    internal fun take(): T? = token.also { token = null }

    companion object {
        fun <T : Any> approved(token: T) = ProjectionApproval(true, token)
        fun <T : Any> denied() = ProjectionApproval<T>(false, null)
    }
}

enum class CaptureState { ACTIVE, WAITING, STOPPED }

data class CapturedPixels(
    val projectionEpoch: String,
    val capturedMonoMs: Long,
    val pixels: FramePixels,
) : AutoCloseable by pixels

/** Holds at most one undelivered frame. take() transfers ownership to the local consumer. */
class ProjectionFrames internal constructor(val epoch: String) : AutoCloseable {
    private var latest: CapturedPixels? = null
    private var closed = false

    @Synchronized
    internal fun publish(frame: CapturedPixels) {
        if (closed || frame.projectionEpoch != epoch) {
            frame.close()
            return
        }
        latest?.close()
        latest = frame
    }

    @Synchronized
    fun take(): CapturedPixels? = latest.also { latest = null }

    @Synchronized
    internal fun discard() {
        latest?.close()
        latest = null
    }

    @Synchronized
    override fun close() {
        closed = true
        discard()
    }
}

data class ProjectionData(
    val projection_epoch: String?,
    val capture_state: CaptureState,
    val frame_source: ProjectionFrames?,
)

internal fun interface ProjectionBackend<T : Any> {
    fun start(token: T, frames: ProjectionFrames, onSystemStop: () -> Unit): AutoCloseable
}

/** This lifecycle is owned by the parent application thread. The backend publishes frames independently. */
internal class ProjectionSession<T : Any>(
    private val backend: ProjectionBackend<T>,
    private val onInvalidated: () -> Unit,
    private val newEpoch: () -> String = { UUID.randomUUID().toString() },
) : AutoCloseable {
    private data class Active(val helpId: String, val frames: ProjectionFrames, val device: AutoCloseable)

    private var active: Active? = null

    fun open(
        helpId: String,
        approval: ProjectionApproval<T>,
        diagnostic: Diagnostic,
        onSystemStopped: (NodeResult<ProjectionData>) -> Unit = {},
    ): NodeResult<ProjectionData> {
        if (diagnostic.basis.help_id != helpId || helpId.isBlank() || diagnostic.basis.consent_revision == null) {
            return failure(diagnostic, "N-07", "open_projection", "STALE_BASIS")
        }
        if (!approval.approved) {
            close()
            return result(diagnostic, "capture_denied", CaptureState.WAITING)
        }
        val token = approval.take()
            ?: return failure(diagnostic, "N-07", "open_projection", "DUPLICATE")
        if (active != null) close()
        val frames = ProjectionFrames(newEpoch())
        val device = try {
            backend.start(token, frames) {
                if (active?.frames === frames) {
                    val stopDiagnostic = diagnostic.copy(
                        run_id = UUID.randomUUID().toString(),
                        basis = diagnostic.basis.copy(projection_epoch = frames.epoch),
                        duration_ms = 0,
                        steps = emptyMap(),
                    )
                    onSystemStopped(stop(helpId, frames.epoch, stopDiagnostic))
                }
            }
        } catch (_: RuntimeException) {
            frames.close()
            onInvalidated()
            return failure(diagnostic, "N-07", "open_projection", "DEPENDENCY_UNAVAILABLE")
        }
        active = Active(helpId, frames, device)
        return result(diagnostic, "capture_started", CaptureState.ACTIVE, frames)
    }

    fun stop(helpId: String, epoch: String, diagnostic: Diagnostic): NodeResult<ProjectionData> {
        if (diagnostic.basis.help_id != helpId || diagnostic.basis.projection_epoch != epoch ||
            active?.helpId != helpId || active?.frames?.epoch != epoch
        ) return failure(diagnostic, "N-07", "stop_projection", "STALE_BASIS")
        close()
        return result(diagnostic, "capture_stopped", CaptureState.STOPPED)
    }

    override fun close() {
        val stopping = active
        active = null
        stopping?.frames?.close()
        try {
            stopping?.device?.close()
        } finally {
            onInvalidated()
        }
    }

    private fun result(
        diagnostic: Diagnostic,
        outcome: String,
        state: CaptureState,
        frames: ProjectionFrames? = null,
    ) = NodeResult(
        NodeStatus.SUCCESS, outcome, ProjectionData(frames?.epoch, state, frames),
        recorded(diagnostic, "N-07", "capture_lifecycle"),
    )
}
