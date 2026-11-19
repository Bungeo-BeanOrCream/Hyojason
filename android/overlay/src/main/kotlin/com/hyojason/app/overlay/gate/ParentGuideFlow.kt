package com.hyojason.app.overlay.gate

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.overlay.surface.ParentSurfaces
import com.hyojason.app.overlay.surface.ParentView
import com.hyojason.app.overlay.surface.SurfaceHost
import com.hyojason.app.overlay.surface.SurfaceInput
import com.hyojason.app.overlay.surface.SurfaceOperation

/** B-06 call site: N-19 decision -> N-20 window. Run on the same serialized loop as [GateClock]. */
object ParentGuideFlow {
    // Gate failures that mean "show nothing now" rather than a broken run.
    private val suppressing = setOf("ALREADY_ENDED", "NO_CONSENT", "PROTECTED_SCREEN", "CAPTURE_GAP", "STALE_BASIS")

    fun run(
        input: GateInput,
        diagnostic: Diagnostic,
        host: SurfaceHost,
        overlayPermission: Boolean,
        clock: GateClock? = null,
        resumeGate: ParentResumeGate? = null,
    ): NodeResult<ParentGuideData> {
        val gate = GuideGate.evaluate(input, diagnostic, resumeGate)
        val base = gate.diagnostics.copy(node_id = "B-06")

        if (gate.status == NodeStatus.ERROR) {
            val code = gate.diagnostics.terminal_error?.code
            host.clear()
            clock?.cancelTimer()
            if (code in suppressing) {
                return NodeResult(NodeStatus.SUCCESS, "suppressed", ParentGuideData(null, false, code!!),
                    base.copy(terminal_error = null))
            }
            return NodeResult(NodeStatus.ERROR, null, null, base)
        }

        val data = requireNotNull(gate.data)
        if (data.cancel_timer) clock?.cancelTimer()
        val display = data.display
        if (display == null) {
            host.clear()
            return NodeResult(NodeStatus.SUCCESS, "suppressed",
                ParentGuideData(null, false, data.reason.name), base)
        }

        val denied = data.reason == GateReason.POLICY_DENIED
        val surface = ParentSurfaces.render(
            SurfaceInput(display, ParentView(display.message ?: "", warning = denied),
                SurfaceOperation.SHOW, null, overlayPermission),
            host, base,
        )
        val merged = base.copy(
            steps = surface.diagnostics.steps,
            terminal_error = surface.diagnostics.terminal_error,
        )
        return when (surface.status) {
            NodeStatus.ERROR -> NodeResult(NodeStatus.ERROR, null, null, merged)
            NodeStatus.PARTIAL -> NodeResult(NodeStatus.PARTIAL, "banner_only",
                ParentGuideData(display, true, data.reason.name), merged)
            else -> NodeResult(NodeStatus.SUCCESS, if (denied) "policy_denied" else "shown",
                ParentGuideData(display, true, data.reason.name), merged)
        }
    }
}
