package com.hyojason.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.overlay.gate.GuideAction
import com.hyojason.app.overlay.gate.LocalGuide

private const val CHANNEL_ID = "settings_guidance"
private const val NOTIFICATION_ID = 302
private const val WARNING = "되돌리기 어려운 동작이에요. 천천히 확인하세요"

private val settingsLabels = mapOf(
    "settings.display" to "디스플레이 및 터치",
    "settings.display_size_text" to "디스플레이 크기 및 텍스트",
    "settings.screen_timeout" to "화면 자동 꺼짐",
    "settings.font_size_larger" to "글꼴 크기 높이기",
    "settings.timeout.10m" to "10분",
)

internal fun settingsGuidanceText(guide: LocalGuide, targetId: String?): String? = when (guide.action) {
    GuideAction.TAP -> settingsLabels[targetId]?.let { "‘$it’ 항목을 눌러 주세요." }
    GuideAction.BACK -> "뒤로 가서 이전 화면으로 돌아가 주세요."
    GuideAction.SCROLL_DOWN -> "화면을 아래로 내려 주세요."
    GuideAction.SCROLL_UP -> "화면을 위로 올려 주세요."
    null -> guide.message?.takeIf { it == WARNING }
    GuideAction.INPUT_HINT, GuideAction.WAIT -> null
}

/**
 * A gate miss caused only by an old frame (slow OCR, not a projection gap) is transient: the next frame
 * re-decides. Cancelling there removed the notice after a few seconds and the same-scope de-dupe never
 * reposted it. Screen changes, new instructions and real gaps still cancel.
 */
internal fun keepSettingsNoticeOn(gateError: String?, health: ObservationHealth): Boolean =
    gateError == "CAPTURE_GAP" && health == ObservationHealth.STABLE

internal class SettingsGuidanceNotification(context: Context) {
    private val app = context.applicationContext
    private val notifications = app.getSystemService(NotificationManager::class.java)

    fun enabled(): Boolean = notifications.areNotificationsEnabled() &&
        notifications.getNotificationChannel(CHANNEL_ID)?.importance != NotificationManager.IMPORTANCE_NONE

    fun show(message: String): Boolean {
        if (!enabled()) return false
        notifications.createNotificationChannel(NotificationChannel(
            CHANNEL_ID, "화면 안내", NotificationManager.IMPORTANCE_HIGH,
        ).apply { lockscreenVisibility = Notification.VISIBILITY_PUBLIC })
        // Android 15 hides heads-up and shows only the redacted public version of non-public
        // notifications while the whole screen is shared, which is exactly when this notice is posted.
        // The text is a fixed allow-listed button name (no screen content), so it is safe to mark public.
        fun builder() = Notification.Builder(app, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("효자손 화면 안내")
            .setContentText(message)
            .setStyle(Notification.BigTextStyle().bigText(message))
            .setCategory(Notification.CATEGORY_STATUS)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
            .setLocalOnly(true)
            .setShowWhen(false)
            .setOnlyAlertOnce(true)
            .setTimeoutAfter(15_000)
        val notice = builder().setPublicVersion(builder().build()).build()
        return try {
            notifications.notify(NOTIFICATION_ID, notice)
            true
        } catch (_: SecurityException) {
            false
        }
    }

    fun clear() = notifications.cancel(NOTIFICATION_ID)
}
