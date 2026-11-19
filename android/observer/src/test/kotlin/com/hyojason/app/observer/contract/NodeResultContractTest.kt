package com.hyojason.app.observer.contract

import java.lang.reflect.Modifier
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class NodeResultContractTest {
    private val basis = Basis(
        pair_id = null,
        call_id = null,
        help_id = null,
        event_seq = null,
        consent_revision = null,
        goal_revision = null,
        mode_revision = null,
        instruction_revision = null,
        projection_epoch = null,
        screen_revision = null,
        utterance_seq = null,
    )

    private val diagnostic = Diagnostic(
        trace_id = "trace-fixture",
        run_id = "run-fixture",
        node_id = "N-19",
        source = Source.FIXTURE,
        basis = basis,
        input_refs = emptyList(),
        duration_ms = 0,
        steps = emptyMap(),
        terminal_error = null,
    )
    private val staleBasis = ErrorDescriptor("STALE_BASIS", "validate_basis", false)

    @Test
    fun contractFieldNames() {
        assertFields(
            Basis::class.java,
            "pair_id", "call_id", "help_id", "event_seq", "consent_revision",
            "goal_revision", "mode_revision", "instruction_revision", "projection_epoch",
            "screen_revision", "utterance_seq",
        )
        assertFields(NodeResult::class.java, "status", "outcome", "data", "diagnostics")
        assertFields(
            Diagnostic::class.java,
            "trace_id", "run_id", "node_id", "source", "basis", "input_refs",
            "duration_ms", "steps", "terminal_error",
        )
        assertFields(ResultRef::class.java, "trace_id", "node_id", "run_id")
        assertFields(StepDiagnostic::class.java, "status", "code", "duration_ms", "count", "retryable")
        assertFields(ErrorDescriptor::class.java, "code", "step", "retryable")
    }

    @Test
    fun basisAcceptsMissingVersionsAndZeroButRejectsNegativeRevision() {
        assertNull(basis.instruction_revision)
        assertEquals(0L, basis.copy(screen_revision = 0).screen_revision)
        assertThrows(IllegalArgumentException::class.java) { basis.copy(event_seq = -1) }
    }

    @Test
    fun successAndPartialRequireOutcomeAndDataWithoutTerminalError() {
        for (status in listOf(NodeStatus.SUCCESS, NodeStatus.PARTIAL)) {
            val result = NodeResult(status, "waiting", emptyList<String>(), diagnostic)
            assertEquals(emptyList<String>(), result.data)
            assertThrows(IllegalArgumentException::class.java) {
                NodeResult<Unit>(status, "waiting", null, diagnostic)
            }
            assertThrows(IllegalArgumentException::class.java) {
                NodeResult(status, null, Unit, diagnostic)
            }
            assertThrows(IllegalArgumentException::class.java) {
                NodeResult(status, "waiting", Unit, diagnostic.copy(terminal_error = staleBasis))
            }
        }
    }

    @Test
    fun errorRequiresTerminalErrorAndNullOutcomeAndData() {
        val failed = diagnostic.copy(terminal_error = staleBasis)
        val result = NodeResult<Unit>(NodeStatus.ERROR, null, null, failed)
        assertNull(result.outcome)
        assertNull(result.data)
        assertThrows(IllegalArgumentException::class.java) {
            NodeResult<Unit>(NodeStatus.ERROR, null, null, diagnostic)
        }
        assertThrows(IllegalArgumentException::class.java) {
            NodeResult(NodeStatus.ERROR, "waiting", Unit, failed)
        }
    }

    @Test
    fun diagnosticCodesAndFixtureProvenance() {
        assertNull(StepDiagnostic(StepStatus.SUCCESS, null).code)
        for (status in listOf(StepStatus.FAILED, StepStatus.SKIPPED)) {
            assertEquals("STALE_BASIS", StepDiagnostic(status, "STALE_BASIS").code)
            assertThrows(IllegalArgumentException::class.java) { StepDiagnostic(status, null) }
        }
        assertEquals(Source.LIVE, Source.LIVE.withInputs(listOf(Source.LIVE)))
        assertEquals(Source.FIXTURE, Source.LIVE.withInputs(listOf(Source.FIXTURE)))
        assertEquals(Source.FIXTURE, Source.FIXTURE.withInputs(emptyList()))
    }

    private fun assertFields(type: Class<*>, vararg expected: String) {
        val fields = type.declaredFields.filterNot { Modifier.isStatic(it.modifiers) }
        assertEquals(expected.toSet(), fields.map { it.name }.toSet())
    }
}
