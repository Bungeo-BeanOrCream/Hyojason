package com.hyojason.app.observer.contract

enum class NodeStatus(val code: String) {
    SUCCESS("success"),
    PARTIAL("partial"),
    ERROR("error"),
}

data class NodeResult<T : Any>(
    val status: NodeStatus,
    val outcome: String?,
    val data: T?,
    val diagnostics: Diagnostic,
) {
    init {
        if (status == NodeStatus.ERROR) {
            require(outcome == null && data == null && diagnostics.terminal_error != null)
        } else {
            require(outcome != null && OUTCOME_CODE.matches(outcome))
            require(data != null && diagnostics.terminal_error == null)
        }
    }

    private companion object {
        val OUTCOME_CODE = Regex("[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
    }
}
