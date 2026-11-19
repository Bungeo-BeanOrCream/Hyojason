package com.hyojason.app.overlay.surface

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.hardware.input.InputManager
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.hyojason.app.observer.LocalRect

/**
 * Narrow TYPE_APPLICATION_OVERLAY windows. The border never takes touches; only the two banner
 * buttons do. Touch pass-through must still be confirmed on the demo device (N-20 completion).
 */
class AndroidSurfaceHost(
    context: Context,
    private val onUserEvent: (SurfaceUserEvent) -> Unit,
) : SurfaceHost {
    private val context = context.applicationContext
    private val windows = this.context.getSystemService(WindowManager::class.java)
    private val maxAlpha = this.context.getSystemService(InputManager::class.java).maximumObscuringOpacityForTouch
    private var border: View? = null
    private var banner: View? = null

    private fun dp(value: Int) = (value * context.resources.displayMetrics.density + 0.5f).toInt()

    private fun params(width: Int, height: Int, touchable: Boolean) = WindowManager.LayoutParams(
        width, height, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            if (touchable) 0 else WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
        PixelFormat.TRANSLUCENT,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        alpha = minOf(0.8f, maxAlpha)
    }

    private fun add(view: View, params: WindowManager.LayoutParams): Boolean = try {
        windows.addView(view, params)
        true
    } catch (_: WindowManager.BadTokenException) {
        false
    } catch (_: SecurityException) {
        false
    }

    private fun remove(view: View?) {
        if (view == null) return
        try {
            windows.removeViewImmediate(view)
        } catch (_: IllegalArgumentException) {
            // Already removed, e.g. after the permission was revoked.
        }
    }

    override fun showBorder(rect: LocalRect): Boolean {
        remove(border); border = null
        val view = View(context).apply {
            background = GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                setStroke(dp(5), Color.RED)
                cornerRadius = dp(8).toFloat()
            }
        }
        val p = params(rect.right - rect.left, rect.bottom - rect.top, touchable = false).apply {
            x = rect.left; y = rect.top
        }
        if (!add(view, p)) return false
        border = view
        return true
    }

    override fun showBanner(text: String): Boolean {
        remove(banner); banner = null
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.BLACK)
            setPadding(dp(12), dp(8), dp(12), dp(8))
            addView(TextView(context).apply {
                this.text = text
                setTextColor(Color.WHITE)
                textSize = 20f
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(Button(context).apply {
                this.text = "끄기"
                setOnClickListener { onUserEvent(SurfaceUserEvent.DISMISS_GUIDE) }
            })
            addView(Button(context).apply {
                this.text = "통화로"
                setOnClickListener { onUserEvent(SurfaceUserEvent.RETURN_TO_CALL) }
            })
        }
        if (!add(row, params(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT, touchable = true))) {
            return false
        }
        banner = row
        return true
    }

    override fun clear() {
        remove(border); border = null
        remove(banner); banner = null
    }
}
