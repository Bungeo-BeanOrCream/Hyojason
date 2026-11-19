package com.hyojason.app.observer.detection

import com.hyojason.app.observer.contract.Diagnostic
import com.hyojason.app.observer.contract.ErrorDescriptor
import com.hyojason.app.observer.contract.NodeResult
import com.hyojason.app.observer.contract.NodeStatus
import com.hyojason.app.observer.contract.StepDiagnostic
import com.hyojason.app.observer.contract.StepStatus

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
