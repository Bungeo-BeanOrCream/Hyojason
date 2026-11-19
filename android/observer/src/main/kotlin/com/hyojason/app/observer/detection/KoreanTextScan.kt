package com.hyojason.app.observer.detection

import android.graphics.Bitmap
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import com.hyojason.app.observer.LocalRect

/** Exposes only exact, profile-approved labels at their local viewport coordinates. */
class KoreanTextScan : AutoCloseable {
    private val recognizer = TextRecognition.getClient(KoreanTextRecognizerOptions.Builder().build())

    fun scan(frame: LocalFrame, profile: DetectionProfile, onResult: (FeatureScan) -> Unit) {
        val pixels = frame.frame_ref?.pixels
        val viewport = frame.viewport
        if (pixels == null || viewport == null || frame.frame_health != FrameHealth.READY) {
            onResult(failed())
            return
        }
        val allowed = profile.screens.flatMap { screen ->
            screen.anchors.flatten() + screen.targets.values.flatten()
        }.filter { it.kind == FeatureKind.TEXT }.associateBy { it.value }
        val buffer = pixels.copyArgb()
        val image = try {
            Bitmap.createBitmap(buffer, pixels.width, pixels.height, Bitmap.Config.ARGB_8888)
        } finally {
            buffer.fill(0)
        }
        val task = try {
            recognizer.process(InputImage.fromBitmap(image, 0))
        } catch (_: RuntimeException) {
            image.recycle()
            onResult(failed())
            return
        }
        task.addOnCompleteListener { result ->
            val features = try {
                if (!result.isSuccessful) failed()
                else {
                    val matches = result.result.textBlocks.flatMap { block -> block.lines }.mapNotNull { line ->
                        val key = allowed[line.text.trim()] ?: return@mapNotNull null
                        val bounds = line.boundingBox ?: return@mapNotNull null
                        if (bounds.isEmpty || bounds.left < 0 || bounds.top < 0 ||
                            bounds.right > pixels.width || bounds.bottom > pixels.height
                        ) return@mapNotNull null
                        FrameFeature(key, LocalRect(
                            viewport.left + bounds.left, viewport.top + bounds.top,
                            viewport.left + bounds.right, viewport.top + bounds.bottom,
                        ))
                    }
                    FeatureScan(FeatureKind.TEXT, true, matches)
                }
            } finally {
                image.recycle()
            }
            onResult(features)
        }
    }

    override fun close() = recognizer.close()

    private fun failed() = FeatureScan(FeatureKind.TEXT, false, emptyList())
}
