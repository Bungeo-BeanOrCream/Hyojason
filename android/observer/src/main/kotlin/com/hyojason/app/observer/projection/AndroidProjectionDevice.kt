package com.hyojason.app.observer.projection

import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import com.hyojason.app.observer.detection.FramePixels

internal class AndroidProjectionDevice(
    private val projection: MediaProjection,
    private val frames: ProjectionFrames,
    private val densityDpi: Int,
    private val onSystemStop: () -> Unit,
    private val onContentInvalidated: () -> Unit,
) : AutoCloseable {
    private val callbacks = Handler(Looper.getMainLooper())
    private val worker = HandlerThread("hyojason-projection").apply { start() }
    private val imageHandler = Handler(worker.looper)
    private val readerLock = Any()
    private var reader: ImageReader? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var contentVisible = true
    private var closed = false
    private var lastFrameMs = -250L
    private val refreshIdleCapture = object : Runnable {
        override fun run() {
            try {
                synchronized(readerLock) {
                    if (closed) return
                    val surface = reader?.surface
                    val display = virtualDisplay
                    trace("tick")
                    if (contentVisible && surface != null && display != null &&
                        SystemClock.elapsedRealtime() - lastFrameMs >= 350L) {
                        trace("refresh")
                        // Static UI can stop producing buffers. Refresh the same approved display
                        // so the safety gate can verify a fresh frame instead of reusing stale pixels.
                        display.surface = null
                        display.surface = surface
                    }
                }
                imageHandler.postDelayed(this, 220L)
            } catch (_: RuntimeException) {
                callbacks.post(onSystemStop)
            }
        }
    }
    private val callback = object : MediaProjection.Callback() {
        override fun onStop() = onSystemStop()

        override fun onCapturedContentResize(width: Int, height: Int) {
            if (closed || width <= 0 || height <= 0) return
            try {
                val resized = synchronized(readerLock) { resize(width, height) }
                // Refreshing the capture surface can report the same size again.
                if (resized) onContentInvalidated()
            } catch (_: RuntimeException) {
                onSystemStop()
            }
        }

        override fun onCapturedContentVisibilityChanged(isVisible: Boolean) {
            synchronized(readerLock) {
                contentVisible = isVisible
                if (!isVisible) frames.discard()
            }
            if (!isVisible) onContentInvalidated()
        }
    }

    fun start(width: Int, height: Int) {
        try {
            projection.registerCallback(callback, callbacks)
            val initial = makeReader(width, height)
            synchronized(readerLock) { reader = initial }
            virtualDisplay = checkNotNull(projection.createVirtualDisplay(
                "Hyojason parent screen", width, height, densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, initial.surface, null, callbacks,
            ))
            imageHandler.postDelayed(refreshIdleCapture, 220L)
        } catch (failure: RuntimeException) {
            close()
            throw failure
        }
    }

    private fun makeReader(width: Int, height: Int): ImageReader =
        ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2).apply {
            setOnImageAvailableListener(::readLatest, imageHandler)
        }

    private fun resize(width: Int, height: Int): Boolean {
        val previous = reader ?: return false
        if (previous.width == width && previous.height == height) return false
        val display = virtualDisplay ?: return false
        val next = makeReader(width, height)
        try {
            display.resize(width, height, densityDpi)
            display.surface = next.surface
        } catch (failure: RuntimeException) {
            next.close()
            throw failure
        }
        reader = next
        frames.discard()
        previous.close()
        return true
    }

    private fun readLatest(source: ImageReader) {
        synchronized(readerLock) {
            if (source !== reader) return trace("stale_reader")
            trace("callback")
            source.acquireLatestImage()?.use { image ->
                val now = SystemClock.elapsedRealtime()
                if (!contentVisible) return trace("hidden_drop")
                if (now - lastFrameMs < 250) return trace("throttle_drop")
                trace("published")
                val pixels = copyPixels(image) ?: return
                lastFrameMs = now
                frames.publish(CapturedPixels(frames.epoch, now, pixels))
            }
        }
    }

    // EXPERIMENT (#37): per-2s counts of capture callbacks. Counts only, no pixels.
    private val traceCounts = mutableMapOf<String, Int>()
    private var traceStart = 0L
    private fun trace(kind: String) {
        traceCounts[kind] = (traceCounts[kind] ?: 0) + 1
        val now = SystemClock.elapsedRealtime()
        if (now - traceStart >= 2_000) {
            if (traceStart != 0L) android.util.Log.i("HyojasonCapture", "window visible=$contentVisible ${traceCounts.toSortedMap()}")
            traceStart = now
            traceCounts.clear()
        }
    }

    private fun copyPixels(image: Image): FramePixels? {
        val plane = image.planes.singleOrNull() ?: return null
        val argb = copyRgbaPlane(plane.buffer, image.width, image.height, plane.rowStride, plane.pixelStride)
            ?: return null
        return FramePixels(image.width, image.height, argb)
    }

    override fun close() {
        if (closed) return
        closed = true
        imageHandler.removeCallbacks(refreshIdleCapture)
        projection.unregisterCallback(callback)
        synchronized(readerLock) {
            virtualDisplay?.release()
            virtualDisplay = null
            val previous = reader
            reader = null
            previous?.close()
        }
        try {
            projection.stop()
        } finally {
            worker.quitSafely()
        }
    }
}
