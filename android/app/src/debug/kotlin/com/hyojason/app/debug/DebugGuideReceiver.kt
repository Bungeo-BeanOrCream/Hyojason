package com.hyojason.app.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.hardware.input.InputManager
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.overlay.surface.AndroidSurfaceHost

/**
 * 디버그 전용(개인 테스트): 터미널(adb broadcast)에서 받은 화면 좌표에 앱의 실제 안내 테두리(AndroidSurfaceHost)를 그린다.
 * 서버·통화 없이 Zoro-1 판단 결과를 부모 화면 테두리로 보기 위한 것. release 빌드에는 포함되지 않는다.
 *
 *  adb shell am broadcast -n com.hyojason.app/.debug.DebugGuideReceiver -a com.hyojason.app.debug.SHOW_GUIDE \
 *      --ei left 0 --ei top 0 --ei right 100 --ei bottom 100 --es text "디스플레이를 눌러 주세요"
 *  adb shell am broadcast -n com.hyojason.app/.debug.DebugGuideReceiver -a com.hyojason.app.debug.SHOW_SCROLL_HINT \
 *      --es direction down --es text "아래로 스크롤해 주세요"      # 버튼이 화면 밖일 때 큰 화살표 + 배너
 *  adb shell am broadcast -n com.hyojason.app/.debug.DebugGuideReceiver -a com.hyojason.app.debug.CLEAR_GUIDE
 * 좌표는 uiautomator bounds(화면 절대 좌표). 오버레이 창은 상태 표시줄 아래부터 배치되므로 그 높이만큼 뺀다.
 */
class DebugGuideReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        val surface = host ?: AndroidSurfaceHost(app) { Log.i(TAG, "user event: $it") }.also { host = it }
        when (intent.action) {
            CLEAR -> { surface.clear(); removeArrow(app); resultData = "cleared" }
            SCROLL -> {
                if (!Settings.canDrawOverlays(app)) {
                    resultData = "no_overlay_permission"; Log.w(TAG, resultData); return
                }
                surface.clear(); removeArrow(app)
                val up = intent.getStringExtra("direction") == "up"
                val arrowShown = showArrow(app, up)
                val banner = surface.showBanner(intent.getStringExtra("text") ?: if (up) "위로 밀어 주세요" else "아래로 스크롤해 주세요")
                resultData = "arrow=$arrowShown banner=$banner"
                Log.i(TAG, resultData)
            }
            SHOW -> {
                if (!Settings.canDrawOverlays(app)) {
                    resultData = "no_overlay_permission"; Log.w(TAG, resultData); return
                }
                val inset = intent.getIntExtra("y_offset", statusBarHeight(app))
                val left = intent.getIntExtra("left", -1).coerceAtLeast(0)
                val top = (intent.getIntExtra("top", -1) - inset).coerceAtLeast(0)
                val right = intent.getIntExtra("right", -1)
                val bottom = intent.getIntExtra("bottom", -1) - inset
                if (right <= left || bottom <= top) {
                    resultData = "bad_rect"; Log.w(TAG, resultData); return
                }
                surface.clear(); removeArrow(app)
                val border = surface.showBorder(LocalRect(left, top, right, bottom))
                val banner = intent.getStringExtra("text")?.let { surface.showBanner(it) } ?: true
                resultData = "border=$border banner=$banner"
                Log.i(TAG, resultData)
            }
        }
    }

    /** 스크롤 방향 안내용 큰 화살표. 앱의 AndroidSurfaceHost에는 화살표가 없어 디버그 전용으로 따로 그린다. */
    private fun showArrow(context: Context, up: Boolean): Boolean {
        val windows = context.getSystemService(WindowManager::class.java)
        val view = TextView(context).apply {
            text = if (up) "⬆" else "⬇"
            textSize = 96f
            setTextColor(Color.parseColor("#D92D43"))
            gravity = Gravity.CENTER
        }
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.CENTER_HORIZONTAL or if (up) Gravity.CENTER_VERTICAL else Gravity.BOTTOM
            y = if (up) 0 else (context.resources.displayMetrics.heightPixels / 8)
            alpha = minOf(0.8f, context.getSystemService(InputManager::class.java).maximumObscuringOpacityForTouch)
        }
        return try {
            windows.addView(view, params); arrow = view; true
        } catch (_: RuntimeException) {
            false
        }
    }

    private fun removeArrow(context: Context) {
        val view = arrow ?: return
        arrow = null
        try {
            context.getSystemService(WindowManager::class.java).removeViewImmediate(view)
        } catch (_: IllegalArgumentException) {
            // 이미 제거됨
        }
    }

    private fun statusBarHeight(context: Context): Int {
        val id = context.resources.getIdentifier("status_bar_height", "dimen", "android")
        return if (id > 0) context.resources.getDimensionPixelSize(id) else 0
    }

    companion object {
        private const val TAG = "HyoDebugGuide"
        const val SHOW = "com.hyojason.app.debug.SHOW_GUIDE"
        const val CLEAR = "com.hyojason.app.debug.CLEAR_GUIDE"
        const val SCROLL = "com.hyojason.app.debug.SHOW_SCROLL_HINT"
        // 같은 프로세스 안에서 테두리 창을 이어서 지우고 바꾸기 위해 하나만 둔다(메인 스레드에서만 접근).
        private var host: AndroidSurfaceHost? = null
        private var arrow: View? = null
    }
}
