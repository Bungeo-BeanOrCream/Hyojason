package com.hyojason.app.observer.projection

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.os.Binder
import android.os.IBinder
import android.os.Looper
import android.view.WindowManager
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.NodeResult
import com.hyojason.app.observer.detection.failure

/** Bind from the parent UI before applying the user's system approval. Keep the binding for the active help. */
class ProjectionCaptureService : Service() {
    private var onCaptureInvalidated: (() -> Unit)? = null
    private val controller = Controller()
    private val session = ProjectionSession(
        backend = ProjectionBackend<Intent>(::startCapture),
        onInvalidated = { onCaptureInvalidated?.invoke() },
    )

    inner class Controller internal constructor() : Binder() {
        fun open(
            helpId: String,
            helpActive: Boolean,
            consentGranted: Boolean,
            approval: ProjectionApproval<Intent>,
            diagnostic: Diagnostic,
            onSystemStopped: (NodeResult<ProjectionData>) -> Unit = {},
            onInvalidated: () -> Unit,
        ): NodeResult<ProjectionData> {
            checkMainThread()
            if (!helpActive || !consentGranted) {
                // An approval obtained outside the active consent scope cannot be used later.
                approval.take()
                session.close()
                return failure(diagnostic, "N-07", "open_projection",
                    if (helpActive) "NO_CONSENT" else "ALREADY_ENDED")
            }
            onCaptureInvalidated = onInvalidated
            return session.open(helpId, approval, diagnostic, onSystemStopped)
        }

        fun stop(helpId: String, epoch: String, diagnostic: Diagnostic): NodeResult<ProjectionData> {
            checkMainThread()
            return session.stop(helpId, epoch, diagnostic)
        }

        fun revokeConsent() {
            checkMainThread()
            session.close()
            onCaptureInvalidated = null
        }
    }

    override fun onBind(intent: Intent): IBinder = controller

    override fun onUnbind(intent: Intent): Boolean {
        controller.revokeConsent()
        return false
    }

    override fun onDestroy() {
        controller.revokeConsent()
        super.onDestroy()
    }

    private fun startCapture(token: Intent, frames: ProjectionFrames, onSystemStop: () -> Unit): AutoCloseable {
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel(
            CHANNEL, "부모 화면 공유", NotificationManager.IMPORTANCE_LOW,
        ))
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle("화면 공유 중")
            .setContentText("도움 종료 또는 화면 공유 중지로 캡처를 끌 수 있어요.")
            .setOngoing(true)
            .build()
        startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        val device = try {
            val manager = getSystemService(MediaProjectionManager::class.java)
            val projection = checkNotNull(manager.getMediaProjection(Activity.RESULT_OK, token))
            val bounds = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
            AndroidProjectionDevice(
                projection, frames, resources.displayMetrics.densityDpi, onSystemStop,
                onContentInvalidated = { onCaptureInvalidated?.invoke() },
            ).also { it.start(bounds.width(), bounds.height()) }
        } catch (failure: RuntimeException) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            throw failure
        }
        return AutoCloseable {
            try {
                device.close()
            } finally {
                stopForeground(STOP_FOREGROUND_REMOVE)
            }
        }
    }

    private fun checkMainThread() {
        check(Looper.myLooper() == Looper.getMainLooper())
    }

    companion object {
        private const val CHANNEL = "hyojason_parent_projection"
        private const val NOTIFICATION_ID = 9107

        fun approvalIntent(context: Context, wholeDisplay: Boolean): Intent {
            val manager = context.getSystemService(MediaProjectionManager::class.java)
            val config = if (wholeDisplay) MediaProjectionConfig.createConfigForDefaultDisplay()
                else MediaProjectionConfig.createConfigForUserChoice()
            return manager.createScreenCaptureIntent(config)
        }

        fun approvalFromResult(resultCode: Int, data: Intent?): ProjectionApproval<Intent> =
            if (resultCode == Activity.RESULT_OK && data != null) ProjectionApproval.approved(data)
            else ProjectionApproval.denied()
    }
}
