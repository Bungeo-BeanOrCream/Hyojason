package com.hyojason.app.overlay.surface

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent

/**
 * Draw-only accessibility service (#37 prototype). It exists only to add TYPE_ACCESSIBILITY_OVERLAY
 * windows, which the Settings app does not hide (HIDE_NON_SYSTEM_OVERLAY_WINDOWS). It subscribes to
 * no events, cannot read window content, and never performs gestures or global actions. Screen
 * observation stays on MediaProjection.
 */
class GuideAccessibilityService : AccessibilityService() {
    override fun onServiceConnected() {
        overlayWindows = getSystemService(WindowManager::class.java)
    }

    override fun onUnbind(intent: Intent?): Boolean {
        overlayWindows = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        overlayWindows = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    companion object {
        /** The service's own WindowManager while the user has it enabled, else null. */
        @Volatile
        var overlayWindows: WindowManager? = null
            private set

        val connected: Boolean get() = overlayWindows != null
    }
}
