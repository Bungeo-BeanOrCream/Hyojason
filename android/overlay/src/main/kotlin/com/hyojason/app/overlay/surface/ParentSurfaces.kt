package com.hyojason.app.overlay.surface

import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.ErrorDescriptor
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.StepDiagnostic
import com.hyojason.app.contract.StepStatus
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.overlay.gate.LocalGuide

enum class SurfaceOperation { SHOW, CLEAR }
enum class VisualKind { BORDER, BANNER, NONE }
enum class SurfaceUserEvent { DISMISS_GUIDE, RETURN_TO_CALL }

/** [warning] shows only the fixed risk sentence; free text never replaces it. */
data class ParentView(val text: String, val warning: Boolean = false)

data class SurfaceInput(
    val display: LocalGuide?,
    val banner: ParentView,
    val operation: SurfaceOperation,
    val decision_id: String?,
    val overlay_permission: Boolean,
)

data class SurfaceData(
    val visible: Boolean,
    val visual_kind: VisualKind,
    val decision_id: String?,
    val user_event: SurfaceUserEvent?,
)

/** Platform windows. Each show* returns false when the OS refuses the window. */
interface SurfaceHost {
    fun showBorder(rect: LocalRect): Boolean
    fun showBanner(text: String): Boolean
    fun clear()
}

object ParentSurfaces {
    const val WARNING = "되돌리기 어려운 동작이에요. 천천히 확인하세요"
    private const val STEP = "render_parent_surfaces"

    /** The caller sets [diagnostic].basis to the scope of this call. */
    fun render(input: SurfaceInput, host: SurfaceHost, diagnostic: Diagnostic): NodeResult<SurfaceData> {
        val base = diagnostic.copy(node_id = "N-20", terminal_error = null)
        fun done(status: NodeStatus, outcome: String, kind: VisualKind, visible: Boolean) =
            NodeResult(status, outcome, SurfaceData(visible, kind, input.decision_id, null), base.copy(
                steps = base.steps + (STEP to StepDiagnostic(StepStatus.SUCCESS, null)),
            ))
        fun fail(code: String): NodeResult<SurfaceData> {
            host.clear()
            return NodeResult(NodeStatus.ERROR, null, null, base.copy(
                steps = base.steps + (STEP to StepDiagnostic(StepStatus.FAILED, code)),
                terminal_error = ErrorDescriptor(code, STEP, false),
            ))
        }

        if (input.operation == SurfaceOperation.CLEAR) {
            host.clear()
            return done(NodeStatus.SUCCESS, "cleared", VisualKind.NONE, false)
        }
        if (!input.overlay_permission) return fail("OVERLAY_DENIED")
        val text = if (input.banner.warning) WARNING else input.display?.message ?: input.banner.text
        if (!host.showBanner(text)) return fail("WINDOW_FAILED")

        val wantsBorder = input.display?.action != null && input.display.message == null
        if (!wantsBorder) return done(NodeStatus.SUCCESS, "shown", VisualKind.BANNER, true)
        val rect = input.display?.rect
        return if (rect != null && host.showBorder(rect)) {
            done(NodeStatus.SUCCESS, "shown", VisualKind.BORDER, true)
        } else {
            done(NodeStatus.PARTIAL, "banner_only", VisualKind.BANNER, true)
        }
    }
}
