package com.hyojason.app.overlay.gate

import com.hyojason.app.observer.LocalObservation
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.ErrorDescriptor
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.StepDiagnostic
import com.hyojason.app.contract.StepStatus

enum class GuideAction { TAP, BACK, SCROLL_DOWN, SCROLL_UP, INPUT_HINT, WAIT }
enum class TargetLocation { VISIBLE, BELOW_FOLD, UNKNOWN, BLOCKED }
enum class Verdict { CORRECT, WRONG, UNKNOWN }
enum class InputEvidence { CONTINUOUS_NO_CHANGE, PENDING, UNKNOWN }

data class CurrentTarget(
    val targetId: String,
    val location: TargetLocation,
    val evidenceCodes: Set<String>,
)

data class ObservedVerdict(val value: Verdict, val observedMonoMs: Long)

data class VerifiedRecovery(val firstEdgeRef: String)

data class PostCallProposal(
    val action: GuideAction,
    val targetId: String?,
    val messageKey: String,
    val receivedMonoMs: Long,
    val validForMs: Long,
)

data class Scoped<out T>(val value: T, val basis: Basis)

data class GateInput(
    val mode: HelpMode,
    val basis: Basis,
    val latestBasis: Basis,
    val latestLayoutRevision: Long?,
    val now_mono_ms: Long,
    val t0_mono_ms: Long?,
    val local_observation: LocalObservation?,
    val inputEvidence: InputEvidence,
    val target: Scoped<CurrentTarget>? = null,
    val verdict: Scoped<ObservedVerdict>? = null,
    val recovery: Scoped<VerifiedRecovery>? = null,
    val proposal: Scoped<PostCallProposal>? = null,
    val helpActive: Boolean = true,
    val consent: Boolean = true,
    val policyDenied: Boolean = false,
)

data class LocalGuide(
    val action: GuideAction?,
    val rect: LocalRect? = null,
    val message: String? = null,
)

enum class GateReason(val outcome: String) {
    WAITING("waiting"),
    FOLLOWED("followed"),
    TAP("intervene_tap"),
    BACK("intervene_back"),
    SCROLL("intervene_scroll"),
    POST_CALL("post_call_guide"),
    POLICY_DENIED("policy_denied"),
}

data class GateData(
    val display: LocalGuide?,
    val elapsed_ms: Long?,
    val reason: GateReason,
    val cancel_timer: Boolean,
)

object GuideGate {
    const val DELAY_MS = 3_000L
    private const val FRAME_MAX_AGE_MS = 1_000L
    private const val STEP = "validate_display"
    private const val WARNING = "되돌리기 어려운 동작이에요. 천천히 확인하세요"

    /**
     * The caller applies observations for this tick before evaluating a deadline at the same time.
     * A pending observation stays pending across ticks until the action verdict arrives.
     */
    fun evaluate(input: GateInput, diagnostic: Diagnostic): NodeResult<GateData> {
        val base = diagnostic.copy(node_id = "N-19", basis = input.basis, terminal_error = null)
        val failure = validate(input)
        if (failure != null) {
            return NodeResult(NodeStatus.ERROR, null, null, base.copy(
                steps = base.steps + (STEP to StepDiagnostic(StepStatus.FAILED, failure)),
                terminal_error = ErrorDescriptor(failure, STEP, false),
            ))
        }

        val elapsed = input.t0_mono_ms?.let { input.now_mono_ms - it }
        val result = decide(input, elapsed)
        return NodeResult(NodeStatus.SUCCESS, result.reason.outcome, result, base.copy(
            steps = base.steps + (STEP to StepDiagnostic(StepStatus.SUCCESS, null)),
        ))
    }

    private fun validate(input: GateInput): String? {
        if (!input.helpActive) return "ALREADY_ENDED"
        if (!input.consent) return "NO_CONSENT"
        if (input.now_mono_ms < 0 || input.t0_mono_ms?.let { it !in 0..input.now_mono_ms } == true) {
            return "INVALID_INPUT"
        }
        val inputs = listOfNotNull(input.target, input.verdict, input.recovery, input.proposal)
        if (!agrees(input.basis, input.latestBasis, input.mode) ||
            inputs.any { !agrees(it.basis, input.latestBasis, input.mode) }
        ) return "STALE_BASIS"

        val observation = input.local_observation ?: return null
        if (observation.screen.observation_health == ObservationHealth.PROTECTED) return "PROTECTED_SCREEN"
        if (observation.screen.observation_health == ObservationHealth.GAP ||
            observation.captured_mono_ms !in 0..input.now_mono_ms ||
            input.now_mono_ms - observation.captured_mono_ms > FRAME_MAX_AGE_MS
        ) return "CAPTURE_GAP"
        if (observation.screen.projection_epoch != input.latestBasis.projection_epoch ||
            observation.screen.screen_revision != input.latestBasis.screen_revision ||
            observation.layout_revision != input.latestLayoutRevision
        ) return "STALE_BASIS"
        return null
    }

    private fun agrees(candidate: Basis, latest: Basis, mode: HelpMode): Boolean {
        if (candidate.pair_id == null || candidate.help_id == null ||
            candidate.consent_revision == null || candidate.mode_revision == null ||
            candidate.projection_epoch == null || candidate.screen_revision == null
        ) return false
        if (candidate.pair_id != latest.pair_id || candidate.call_id != latest.call_id ||
            candidate.help_id != latest.help_id || candidate.consent_revision != latest.consent_revision ||
            candidate.mode_revision != latest.mode_revision || candidate.projection_epoch != latest.projection_epoch ||
            candidate.screen_revision != latest.screen_revision
        ) return false
        return when (mode) {
            HelpMode.CALL_CHILD_LED -> candidate.instruction_revision != null &&
                candidate.instruction_revision == latest.instruction_revision
            HelpMode.POST_CALL_AGENT -> candidate.goal_revision != null &&
                candidate.goal_revision == latest.goal_revision
        }
    }

    private fun decide(input: GateInput, elapsed: Long?): GateData {
        val waiting = GateData(null, elapsed, GateReason.WAITING, cancel_timer = false)
        val observation = input.local_observation ?: return waiting
        if (observation.screen.observation_health != ObservationHealth.STABLE) return waiting
        if (input.policyDenied) {
            return GateData(LocalGuide(null, message = WARNING), elapsed, GateReason.POLICY_DENIED, true)
        }

        val verdict = input.verdict?.value
        val t0 = input.t0_mono_ms ?: return waiting
        val verifiedVerdict = verdict?.takeIf { it.observedMonoMs in t0..input.now_mono_ms }
        if (input.mode == HelpMode.CALL_CHILD_LED && verifiedVerdict?.value == Verdict.CORRECT) {
            return GateData(null, elapsed, GateReason.FOLLOWED, true)
        }
        if (elapsed == null || elapsed < DELAY_MS || input.inputEvidence == InputEvidence.PENDING) return waiting

        if (input.mode == HelpMode.POST_CALL_AGENT) {
            val proposal = input.proposal?.value ?: return waiting
            if (proposal.receivedMonoMs !in 0..input.now_mono_ms ||
                input.now_mono_ms - proposal.receivedMonoMs >= proposal.validForMs
            ) return waiting
            val guide = when (proposal.action) {
                GuideAction.WAIT -> null
                GuideAction.TAP -> findRect(observation, proposal.targetId)?.let { LocalGuide(GuideAction.TAP, it) }
                else -> LocalGuide(proposal.action, message = proposal.messageKey)
            } ?: return waiting
            return GateData(guide, elapsed, GateReason.POST_CALL, true)
        }

        if (verifiedVerdict?.value == Verdict.WRONG && input.recovery?.value?.firstEdgeRef?.isNotBlank() == true) {
            return GateData(LocalGuide(GuideAction.BACK, message = "뒤로 가기"), elapsed, GateReason.BACK, true)
        }
        if (verdict != null || input.inputEvidence != InputEvidence.CONTINUOUS_NO_CHANGE) return waiting
        val target = input.target?.value ?: return waiting
        if (target.evidenceCodes.isEmpty()) return waiting
        return when (target.location) {
            TargetLocation.VISIBLE -> findRect(observation, target.targetId)?.let {
                GateData(LocalGuide(GuideAction.TAP, it), elapsed, GateReason.TAP, true)
            } ?: waiting
            TargetLocation.BELOW_FOLD -> GateData(
                LocalGuide(GuideAction.SCROLL_DOWN, message = "화면 내리기"), elapsed, GateReason.SCROLL, true,
            )
            TargetLocation.UNKNOWN, TargetLocation.BLOCKED -> waiting
        }
    }

    private fun findRect(observation: LocalObservation, targetId: String?): LocalRect? =
        observation.targets.singleOrNull { it.target_id == targetId && it.evidence_codes.isNotEmpty() }?.rect
}
