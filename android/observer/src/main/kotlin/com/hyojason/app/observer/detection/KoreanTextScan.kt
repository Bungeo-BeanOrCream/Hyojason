package com.hyojason.app.observer.detection

import android.graphics.Bitmap
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.korean.KoreanTextRecognizerOptions
import com.hyojason.app.observer.LocalRect

/** Exposes only exact, profile-approved labels at their local viewport coordinates. */
class KoreanTextScan(private val maxWidth: Int = Int.MAX_VALUE) : AutoCloseable {
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
        val original = try {
            Bitmap.createBitmap(buffer, pixels.width, pixels.height, Bitmap.Config.ARGB_8888)
        } finally {
            buffer.fill(0)
        }
        val image = if (original.width > maxWidth) {
            Bitmap.createScaledBitmap(original, maxWidth, maxOf(1, original.height * maxWidth / original.width), true)
                .also { original.recycle() }
        } else original
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
                            bounds.right > image.width || bounds.bottom > image.height
                        ) return@mapNotNull null
                        FrameFeature(key, LocalRect(
                            viewport.left + bounds.left * pixels.width / image.width,
                            viewport.top + bounds.top * pixels.height / image.height,
                            viewport.left + (bounds.right * pixels.width + image.width - 1) / image.width,
                            viewport.top + (bounds.bottom * pixels.height + image.height - 1) / image.height,
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
