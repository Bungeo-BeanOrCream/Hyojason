package com.hyojason.app.overlay.surface

import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.NodeStatus
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.overlay.gate.GuideAction
import com.hyojason.app.overlay.gate.LocalGuide
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class ParentSurfacesTest {
    private val basis = Basis("pair", "call", "help", 9, 1, 1, 1, 1, "epoch", 3, 1)
    private val diagnostic = Diagnostic("trace-fixture", "run-fixture", "N-20", Source.FIXTURE, basis, emptyList(), 0, emptyMap(), null)
    private val rect = LocalRect(30, 120, 110, 180)

    private class FakeHost(var borderOk: Boolean = true, var bannerOk: Boolean = true) : SurfaceHost {
        var border: LocalRect? = null
        var banner: String? = null
        override fun showBorder(rect: LocalRect) = borderOk.also { if (it) border = rect }
        override fun showBanner(text: String) = bannerOk.also { if (it) banner = text }
        override fun clear() { border = null; banner = null }
    }

    private fun input(
        display: LocalGuide? = LocalGuide(GuideAction.TAP, rect),
        op: SurfaceOperation = SurfaceOperation.SHOW,
        permission: Boolean = true,
        warning: Boolean = false,
    ) = SurfaceInput(display, ParentView("여기를 눌러 보세요", warning), op, "d1", permission)

    @Test fun case20_1_targetShowsBorderAndBanner() {
        val host = FakeHost()
        val result = ParentSurfaces.render(input(), host, diagnostic)
        assertEquals(NodeStatus.SUCCESS, result.status)
        assertEquals("shown", result.outcome)
        assertEquals(VisualKind.BORDER, result.data?.visual_kind)
        assertEquals(rect, host.border)
        assertEquals("N-20", result.diagnostics.node_id)
    }

    @Test fun case20_1_fallbackMessageShowsBannerOnly() {
        val host = FakeHost()
        val result = ParentSurfaces.render(input(LocalGuide(GuideAction.SCROLL_DOWN, message = "화면 내리기")), host, diagnostic)
        assertEquals("shown", result.outcome)
        assertEquals(VisualKind.BANNER, result.data?.visual_kind)
        assertEquals("화면 내리기", host.banner)
        assertNull(host.border)
    }

    @Test fun case20_2_clearRemovesEverything() {
        val host = FakeHost()
        ParentSurfaces.render(input(), host, diagnostic)
        val result = ParentSurfaces.render(input(op = SurfaceOperation.CLEAR), host, diagnostic)
        assertEquals("cleared", result.outcome)
        assertFalse(result.data!!.visible)
        assertNull(host.border)
        assertNull(host.banner)
    }

    @Test fun case20_3_missingOrRefusedBorderIsBannerOnly() {
        for ((display, host) in listOf(
            LocalGuide(GuideAction.TAP, null) to FakeHost(),
            LocalGuide(GuideAction.TAP, rect) to FakeHost(borderOk = false),
        )) {
            val result = ParentSurfaces.render(input(display), host, diagnostic)
            assertEquals(NodeStatus.PARTIAL, result.status)
            assertEquals("banner_only", result.outcome)
            assertEquals(VisualKind.BANNER, result.data?.visual_kind)
        }
    }

    @Test fun case20_4_deniedPermissionOrBannerFailureIsErrorAndClears() {
        for ((permission, host, code) in listOf(
            Triple(false, FakeHost(), "OVERLAY_DENIED"),
            Triple(true, FakeHost(bannerOk = false), "WINDOW_FAILED"),
        )) {
            host.border = rect
            val result = ParentSurfaces.render(input(permission = permission), host, diagnostic)
            assertEquals(NodeStatus.ERROR, result.status)
            assertNull(result.outcome)
            assertNull(result.data)
            assertEquals(code, result.diagnostics.terminal_error?.code)
            assertNull(host.border)
            assertNull(host.banner)
        }
    }

    @Test fun case20_5_basisMatchesCallAndWarningUsesFixedSentence() {
        val host = FakeHost()
        val result = ParentSurfaces.render(input(warning = true), host, diagnostic)
        assertEquals(basis, result.diagnostics.basis)
        assertEquals("되돌리기 어려운 동작이에요. 천천히 확인하세요", host.banner)
        assertNull(result.diagnostics.terminal_error)
    }
}
