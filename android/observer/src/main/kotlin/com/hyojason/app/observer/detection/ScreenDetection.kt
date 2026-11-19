package com.hyojason.app.observer.detection

import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.LocalTarget
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.observer.ScreenSample
import com.hyojason.app.observer.SemanticScreen
import com.hyojason.app.observer.contract.Diagnostic
import com.hyojason.app.observer.contract.NodeResult
import com.hyojason.app.observer.contract.NodeStatus
import com.hyojason.app.observer.contract.Source

enum class FeatureKind { TEXT, ICON, STATE }
enum class DetectionCoverage { COMPLETE, PARTIAL }

data class FeatureKey(val kind: FeatureKind, val value: String)
data class FrameFeature(val key: FeatureKey, val rect: LocalRect)

/** Failed paths contribute no features. Each successful path only supplies its own feature kind. */
data class FeatureScan(val kind: FeatureKind, val succeeded: Boolean, val features: List<FrameFeature>) {
    init {
        require(features.all { it.key.kind == kind })
        require(succeeded || features.isEmpty())
    }
}

data class DetectionInput(
    val local_frame: LocalFrame,
    val profile_id: String,
    val profile_revision: Int,
    val screen_hint: String?,
)

data class DetectionData(
    val screen_kind: String,
    val targets: List<LocalTarget>,
    val verified_signals: Set<String>,
    val coverage: DetectionCoverage,
    val frame_health: FrameHealth,
)

data class ProfileScreen(
    val kind: String,
    val anchors: List<Set<FeatureKey>>,
    val targets: Map<String, Set<FeatureKey>>,
    val signals: Map<String, FeatureKey> = emptyMap(),
) {
    init {
        require(anchors.isNotEmpty() && anchors.all { it.isNotEmpty() })
    }
}

class DetectionProfile(
    val id: String,
    val revision: Int,
    val screens: List<ProfileScreen>,
)

class ScreenDetection(private val profiles: List<DetectionProfile>) {
    fun detect(
        input: DetectionInput,
        scans: List<FeatureScan>,
        diagnostic: Diagnostic,
    ): NodeResult<DetectionData> {
        val profile = profile(input, diagnostic)
            ?: return failure(diagnostic, "N-09", "match_profile", "INVALID_INPUT")
        val viewport = input.local_frame.viewport
        val pixels = input.local_frame.frame_ref
        if (viewport == null || pixels == null || input.local_frame.frame_health != FrameHealth.READY) {
            return failure(diagnostic, "N-09", "detect_targets", "CAPTURE_GAP")
        }
        if (scans.none { it.succeeded }) {
            return failure(diagnostic, "N-09", "detect_targets", "DEPENDENCY_UNAVAILABLE")
        }

        val features = scans.flatMap { it.features }.filter { feature ->
            viewport.contains(feature.rect) && pixels.masks.none { it.intersects(feature.rect) }
        }
        val keys = features.mapTo(mutableSetOf()) { it.key }
        val screen = profile.screens.filter { candidate ->
            (input.screen_hint == null || input.screen_hint == candidate.kind) &&
                candidate.anchors.any { keys.containsAll(it) }
        }.singleOrNull()
        val targets = screen?.targets.orEmpty().mapNotNull { (id, aliases) ->
            val matches = features.filter { it.key in aliases }.map { it.rect }.distinct()
            matches.singleOrNull()?.let { LocalTarget(id, it, setOf("PROFILE_MATCH")) }
        }
        val partial = scans.any { !it.succeeded }
        val outcome = when {
            partial -> "observation_partial"
            targets.isEmpty() -> "no_targets"
            else -> "targets_found"
        }
        return NodeResult(
            if (partial) NodeStatus.PARTIAL else NodeStatus.SUCCESS, outcome,
            DetectionData(
                screen?.kind ?: "unknown", targets,
                screen?.signals.orEmpty().filterValues { it in keys }.keys.toSet(),
                if (partial) DetectionCoverage.PARTIAL else DetectionCoverage.COMPLETE,
                FrameHealth.READY,
            ),
            recorded(diagnostic, "N-09", "detect_targets", "DEPENDENCY_UNAVAILABLE".takeIf { partial }),
        )
    }

    fun sample(input: DetectionInput, data: DetectionData, projectionEpoch: String): ScreenSample {
        val profile = profiles.single { it.id == input.profile_id && it.revision == input.profile_revision }
        val screen = profile.screens.singleOrNull { it.kind == data.screen_kind }
        return ScreenSample(
            projectionEpoch, input.local_frame.captured_mono_ms,
            SemanticScreen(data.screen_kind, screen?.targets.orEmpty().keys.toSet() + data.verified_signals),
            input.local_frame.layout_revision, data.targets, data.verified_signals,
            if (screen != null && data.coverage == DetectionCoverage.COMPLETE) ObservationHealth.STABLE
            else ObservationHealth.GAP,
        )
    }

    private fun profile(input: DetectionInput, diagnostic: Diagnostic): DetectionProfile? =
        profiles.singleOrNull {
            it.id == input.profile_id && it.revision == input.profile_revision &&
                (it.id != DemoProfiles.fixture.id || diagnostic.source == Source.FIXTURE)
        }
}
