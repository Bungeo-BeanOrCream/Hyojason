package com.hyojason.app.overlay.gate

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus

data class ParentGuideData(val display: LocalGuide?, val rendered: Boolean, val reason: String)

/** The parent event loop must clear its display before publishing the suppression. */
fun suppressStaleBasis(
    gate: NodeResult<GateData>,
    diagnostic: Diagnostic,
    clearDisplay: () -> Unit,
): NodeResult<ParentGuideData>? {
    if (gate.diagnostics.terminal_error?.code != "STALE_BASIS") return null
    clearDisplay()
    return NodeResult(
        NodeStatus.SUCCESS, "suppressed", ParentGuideData(null, false, "STALE_BASIS"),
        diagnostic.copy(node_id = "B-06", terminal_error = null),
    )
}
