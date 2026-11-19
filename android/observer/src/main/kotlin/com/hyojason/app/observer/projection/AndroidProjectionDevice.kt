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
    private val callback = object : MediaProjection.Callback() {
        override fun onStop() = onSystemStop()

        override fun onCapturedContentResize(width: Int, height: Int) {
            if (closed || width <= 0 || height <= 0) return
            try {
                synchronized(readerLock) { resize(width, height) }
                onContentInvalidated()
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
        } catch (failure: RuntimeException) {
            close()
            throw failure
        }
    }

    private fun makeReader(width: Int, height: Int): ImageReader =
        ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2).apply {
            setOnImageAvailableListener(::readLatest, imageHandler)
        }

    private fun resize(width: Int, height: Int) {
        val previous = reader ?: return
        if (previous.width == width && previous.height == height) return
        val display = virtualDisplay ?: return
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
    }

    private fun readLatest(source: ImageReader) {
        synchronized(readerLock) {
            if (source !== reader) return
            source.acquireLatestImage()?.use { image ->
                val now = SystemClock.elapsedRealtime()
                if (!contentVisible || now - lastFrameMs < 250) return
                val pixels = copyPixels(image) ?: return
                lastFrameMs = now
                frames.publish(CapturedPixels(frames.epoch, now, pixels))
            }
        }
    }

    private fun copyPixels(image: Image): FramePixels? {
        val plane = image.planes.singleOrNull() ?: return null
        val buffer = plane.buffer
        val lastPixel = (image.height - 1L) * plane.rowStride + (image.width - 1L) * plane.pixelStride + 4
        val pixelCount = image.width.toLong() * image.height
        if (plane.pixelStride < 4 || lastPixel > buffer.limit() || pixelCount !in 1..Int.MAX_VALUE.toLong()) {
            return null
        }
        val argb = IntArray(pixelCount.toInt())
        for (y in 0 until image.height) {
            for (x in 0 until image.width) {
                val offset = y * plane.rowStride + x * plane.pixelStride
                val red = buffer.get(offset).toInt() and 0xff
                val green = buffer.get(offset + 1).toInt() and 0xff
                val blue = buffer.get(offset + 2).toInt() and 0xff
                val alpha = buffer.get(offset + 3).toInt() and 0xff
                argb[y * image.width + x] = (alpha shl 24) or (red shl 16) or (green shl 8) or blue
            }
        }
        return FramePixels(image.width, image.height, argb)
    }

    override fun close() {
        if (closed) return
        closed = true
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
