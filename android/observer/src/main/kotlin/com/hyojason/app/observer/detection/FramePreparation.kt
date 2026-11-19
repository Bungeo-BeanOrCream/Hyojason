package com.hyojason.app.observer.detection

import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.contract.Diagnostic
import com.hyojason.app.observer.contract.NodeResult
import com.hyojason.app.observer.contract.NodeStatus

enum class FrameSafety { ALLOWED, PROTECTED, UNKNOWN }
enum class FrameHealth { READY, PROTECTED, GEOMETRY_UNCERTAIN }

/** Ownership of the array moves to this reference. Closing wipes it. */
class FramePixels(val width: Int, val height: Int, private val argb: IntArray) : AutoCloseable {
    private var closed = false

    init {
        require(width > 0 && height > 0 && width.toLong() * height == argb.size.toLong())
    }

    internal fun isBlank(): Boolean {
        check(!closed)
        return argb.all { (it and 0x00ffffff) == 0 }
    }

    internal fun crop(bufferBounds: LocalRect, viewport: LocalRect, masks: List<LocalRect>): FramePixels {
        check(!closed)
        val width = viewport.right - viewport.left
        val height = viewport.bottom - viewport.top
        val copy = IntArray(width * height)
        for (y in viewport.top until viewport.bottom) {
            val start = (y - bufferBounds.top) * this.width + viewport.left - bufferBounds.left
            argb.copyInto(copy, (y - viewport.top) * width, start, start + width)
        }
        for (mask in masks) {
            val left = maxOf(mask.left, viewport.left)
            val right = minOf(mask.right, viewport.right)
            if (right <= left) continue
            for (y in maxOf(mask.top, viewport.top) until minOf(mask.bottom, viewport.bottom)) {
                val row = (y - viewport.top) * width - viewport.left
                copy.fill(0xff000000.toInt(), row + left, row + right)
            }
        }
        return FramePixels(width, height, copy)
    }

    internal fun copyArgb(): IntArray {
        check(!closed)
        return argb.copyOf()
    }

    override fun close() {
        argb.fill(0)
        closed = true
    }
}

data class CaptureFrame(
    val projection_epoch: String,
    val frame_ref: FramePixels,
    val captured_mono_ms: Long,
    val bounds: LocalRect,
    val viewport: LocalRect?,
    val rotation: Int,
    val overlay_masks: List<LocalRect>,
    val safety: FrameSafety,
)

class PreparedPixels internal constructor(
    val pixels: FramePixels,
    internal val masks: List<LocalRect>,
) : AutoCloseable by pixels

data class LocalFrame(
    val frame_ref: PreparedPixels?,
    val viewport: LocalRect?,
    val layout_revision: Long,
    val frame_health: FrameHealth,
    val captured_mono_ms: Long,
) : AutoCloseable {
    override fun close() {
        frame_ref?.close()
    }
}

class FramePreparation(private val projectionEpoch: String) {
    private data class Geometry(val bounds: LocalRect, val viewport: LocalRect, val rotation: Int)

    private var geometry: Geometry? = null
    private var layoutRevision = 0L
    private var lastCapture = -1L

    /** Source pixels are released on every path. The caller owns any returned LocalFrame. */
    fun prepare(input: CaptureFrame, diagnostic: Diagnostic): NodeResult<LocalFrame> = input.frame_ref.use { pixels ->
        if (input.projection_epoch != projectionEpoch || diagnostic.basis.projection_epoch != projectionEpoch) {
            return failure(diagnostic, "N-08", "prepare_frame", "STALE_BASIS")
        }
        if (input.captured_mono_ms <= lastCapture || input.rotation !in 0..3 ||
            input.bounds.right - input.bounds.left != pixels.width ||
            input.bounds.bottom - input.bounds.top != pixels.height
        ) return failure(diagnostic, "N-08", "prepare_frame", "INVALID_INPUT")
        lastCapture = input.captured_mono_ms

        if (input.safety != FrameSafety.ALLOWED || pixels.isBlank()) {
            return result(input, diagnostic, FrameHealth.PROTECTED, NodeStatus.SUCCESS, "protected_screen")
        }
        val viewport = input.viewport
        if (viewport == null || !input.bounds.contains(viewport)) {
            return result(input, diagnostic, FrameHealth.GEOMETRY_UNCERTAIN, NodeStatus.PARTIAL, "geometry_uncertain")
        }
        val next = Geometry(input.bounds, viewport, input.rotation)
        if (next != geometry) layoutRevision++
        geometry = next
        val normalized = PreparedPixels(pixels.crop(input.bounds, viewport, input.overlay_masks), input.overlay_masks.toList())
        result(input, diagnostic, FrameHealth.READY, NodeStatus.SUCCESS, "frame_ready", normalized, viewport)
    }

    private fun result(
        input: CaptureFrame,
        diagnostic: Diagnostic,
        health: FrameHealth,
        status: NodeStatus,
        outcome: String,
        pixels: PreparedPixels? = null,
        viewport: LocalRect? = null,
    ) = NodeResult(status, outcome, LocalFrame(pixels, viewport, layoutRevision, health, input.captured_mono_ms),
        recorded(diagnostic, "N-08", "prepare_frame"))
}

internal fun LocalRect.contains(other: LocalRect): Boolean =
    other.left >= left && other.top >= top && other.right <= right && other.bottom <= bottom

internal fun LocalRect.intersects(other: LocalRect): Boolean =
    left < other.right && other.left < right && top < other.bottom && other.top < bottom
