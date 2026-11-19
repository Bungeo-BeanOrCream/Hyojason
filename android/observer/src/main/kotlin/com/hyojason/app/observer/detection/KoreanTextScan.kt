package com.hyojason.app.observer.detection

import android.graphics.Bitmap
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import com.hyojason.app.observer.LocalRect

/** Exposes only exact, profile-approved labels at their local viewport coordinates. */
class KoreanTextScan : AutoCloseable {
    private val recognizer = TextRecognition.getClient(KoreanTextRecognizerOptions.Builder().build())

    fun scan(input: DetectionInput, profile: DetectionProfile, onResult: (FeatureScan) -> Unit) {
        val frame = input.local_frame
        val pixels = frame.frame_ref?.pixels
        val viewport = frame.viewport
        if (pixels == null || viewport == null || frame.frame_health != FrameHealth.READY) {
            onResult(failed())
            return
        }
        if (input.profile_id != profile.id || input.profile_revision != profile.revision) {
            onResult(failed())
            return
        }
        if (input.profile_version != profile.profile_version) {
            onResult(FeatureScan(FeatureKind.TEXT, true, emptyList()))
            return
        }
        val allowed = profile.screens.flatMap { screen ->
            screen.anchors.flatten() + screen.targets.values.flatMap { rule ->
                rule.aliases + listOfNotNull(rule.rowMarker) + rule.requiredFeatures
            }
        }.filter { it.kind == FeatureKind.TEXT }.distinct().groupBy { normalized(it.value) }
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
                        val key = allowed[normalized(line.text)]?.singleOrNull() ?: return@mapNotNull null
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

    private fun normalized(text: String) = text.filterNot(Char::isWhitespace)

    private fun failed() = FeatureScan(FeatureKind.TEXT, false, emptyList())
}
