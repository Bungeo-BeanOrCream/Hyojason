package com.hyojason.app.debug

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.hardware.input.InputManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

internal const val CAPTURE_REQUEST_CODE = 107

internal interface CaptureControls : AutoCloseable {
    fun requestApproval()
    fun acceptApproval(resultCode: Int, data: Intent?)
    fun stop()
}

/** Temporary AVD verification UI. The integrated application will provide its own activities. */
class VerificationActivity : Activity() {
    private lateinit var captureStatus: TextView
    private lateinit var overlayStatus: TextView
    private var capture: CaptureControls? = null
    private var testOverlay: View? = null
    private val overlayWindowManager by lazy { getSystemService(WindowManager::class.java) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            fitsSystemWindows = true
            setPadding(dp(24), dp(24), dp(24), dp(24))
        }
        content.addView(TextView(this).apply {
            text = "AVD 검증"
            textSize = 24f
        })
        captureStatus = TextView(this).apply { textSize = 17f }
        overlayStatus = TextView(this).apply { textSize = 17f }
        capture = createCaptureControls(this) { captureStatus.text = it }
        if (capture == null) captureStatus.text = "캡처 모듈 연결 대기"
        content.addView(captureStatus)
        content.button("화면 공유 시작") { capture?.requestApproval() }.isEnabled = capture != null
        content.button("화면 공유 중지") { capture?.stop() }.isEnabled = capture != null
        content.addView(overlayStatus)
        content.button("오버레이 권한 설정") {
            clearTestOverlay()
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        content.button("테스트 테두리 표시") { showTestOverlay() }
        content.button("테스트 테두리 제거") { clearTestOverlay() }
        setContentView(content)
    }

    override fun onResume() {
        super.onResume()
        if (!Settings.canDrawOverlays(this)) clearTestOverlay()
        refreshOverlayStatus()
    }

    @Deprecated("The debug screen receives the platform consent result directly.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        @Suppress("DEPRECATION")
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == CAPTURE_REQUEST_CODE) capture?.acceptApproval(resultCode, data)
    }

    private fun showTestOverlay() {
        if (overlayOwner != null || !Settings.canDrawOverlays(this)) {
            refreshOverlayStatus()
            return
        }
        val rectangle = View(this).apply {
            background = GradientDrawable().apply {
                setColor(Color.TRANSPARENT)
                setStroke(dp(4), Color.RED)
                cornerRadius = dp(8).toFloat()
            }
        }
        val params = WindowManager.LayoutParams(
            dp(144), dp(96), WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.CENTER
            alpha = minOf(0.65f, getSystemService(InputManager::class.java).maximumObscuringOpacityForTouch)
        }
        try {
            overlayWindowManager.addView(rectangle, params)
            testOverlay = rectangle
            overlayOwner = this
            refreshOverlayStatus()
        } catch (_: WindowManager.BadTokenException) {
            overlayStatus.text = "테스트 테두리를 표시할 수 없어요."
        } catch (_: SecurityException) {
            overlayStatus.text = "오버레이 권한을 확인해 주세요."
        }
    }

    private fun clearTestOverlay() {
        overlayOwner?.removeOwnedOverlay()
        refreshOverlayStatus()
    }

    private fun removeOwnedOverlay() {
        val rectangle = testOverlay
        testOverlay = null
        overlayOwner = null
        if (rectangle != null) {
            try {
                overlayWindowManager.removeViewImmediate(rectangle)
            } catch (_: IllegalArgumentException) {
                // Android may have already removed the window after permission was revoked.
            }
        }
    }

    private fun refreshOverlayStatus() {
        overlayStatus.text = when {
            overlayOwner != null -> "테스트 테두리 표시 중"
            Settings.canDrawOverlays(this) -> "오버레이 권한 허용됨"
            else -> "오버레이 권한 필요"
        }
    }

    private fun LinearLayout.button(label: String, action: () -> Unit): Button =
        Button(context).apply {
            text = label
            setOnClickListener { action() }
            this@button.addView(this)
        }

    private fun dp(value: Int) = (value * resources.displayMetrics.density + 0.5f).toInt()

    override fun onDestroy() {
        if (overlayOwner === this) removeOwnedOverlay()
        capture?.close()
        super.onDestroy()
    }

    private companion object {
        // All launcher instances share one non-touchable overlay on the main thread.
        var overlayOwner: VerificationActivity? = null
    }
}
