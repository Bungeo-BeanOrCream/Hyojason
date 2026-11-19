package com.hyojason.app.observer.detection

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.ErrorDescriptor
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.StepDiagnostic
import com.hyojason.app.contract.StepStatus

internal fun recorded(diagnostic: Diagnostic, node: String, step: String, code: String? = null): Diagnostic =
    diagnostic.copy(
        node_id = node,
        steps = diagnostic.steps + (step to StepDiagnostic(
            if (code == null) StepStatus.SUCCESS else StepStatus.FAILED, code,
        )),
        terminal_error = null,
    )

internal fun <T : Any> failure(diagnostic: Diagnostic, node: String, step: String, code: String): NodeResult<T> =
    NodeResult(NodeStatus.ERROR, null, null,
        recorded(diagnostic, node, step, code).copy(terminal_error = ErrorDescriptor(code, step, false)))
