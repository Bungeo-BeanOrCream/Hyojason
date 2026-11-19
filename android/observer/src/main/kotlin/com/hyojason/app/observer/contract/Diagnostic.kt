package com.hyojason.app.observer.contract

enum class Source {
    LIVE,
    FIXTURE;

    fun withInputs(inputs: Iterable<Source>): Source =
        if (Source.FIXTURE in inputs) Source.FIXTURE else this
}

data class ResultRef(
    val trace_id: String,
    val node_id: String,
    val run_id: String,
)

enum class StepStatus(val code: String) {
    SUCCESS("success"),
    FAILED("failed"),
    SKIPPED("skipped"),
}

data class StepDiagnostic(
    val status: StepStatus,
    val code: String?,
    val duration_ms: Long? = null,
    val count: Long? = null,
    val retryable: Boolean? = null,
) {
    init {
        require(code != null || status == StepStatus.SUCCESS)
        require(code == null || DIAGNOSTIC_CODE.matches(code))
        require(duration_ms == null || duration_ms >= 0)
        require(count == null || count >= 0)
    }
}

data class ErrorDescriptor(
    val code: String,
    val step: String,
    val retryable: Boolean,
) {
    init {
        require(DIAGNOSTIC_CODE.matches(code))
    }
}

data class Diagnostic(
    val trace_id: String,
    val run_id: String,
    val node_id: String,
    val source: Source,
    val basis: Basis,
    val input_refs: List<ResultRef>,
    val duration_ms: Long,
    val steps: Map<String, StepDiagnostic>,
    val terminal_error: ErrorDescriptor?,
) {
    init {
        require(duration_ms >= 0)
    }
}

private val DIAGNOSTIC_CODE = Regex("[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
