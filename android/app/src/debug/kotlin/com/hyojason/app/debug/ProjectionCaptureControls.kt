package com.hyojason.app.debug

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.projection.ProjectionCaptureService
import com.hyojason.app.observer.projection.ProjectionData
import com.hyojason.app.observer.projection.ProjectionFrames
import com.hyojason.app.contract.NodeResult

internal fun createCaptureControls(activity: Activity, onStatus: (String) -> Unit): CaptureControls? =
    ProjectionCaptureControls(activity, onStatus)

private class ProjectionCaptureControls(
    private val activity: Activity,
    private val onStatus: (String) -> Unit,
) : CaptureControls {
    private val handler = Handler(Looper.getMainLooper())
    private var controller: ProjectionCaptureService.Controller? = null
    private var frames: ProjectionFrames? = null
    private var frameCount = 0L
    private var sequence = 0L
    private var status = "화면 공유 서비스 연결 중"

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, service: IBinder) {
            controller = service as ProjectionCaptureService.Controller
            update("화면 공유 승인을 요청할 수 있어요.")
        }

        override fun onServiceDisconnected(name: ComponentName) {
            controller = null
            frames = null
            update("화면 공유 서비스 연결이 끊겼어요.")
        }
    }
    private val poll = object : Runnable {
        override fun run() {
            frames?.take()?.use {
                frameCount++
                update(status)
            }
            handler.postDelayed(this, 250)
        }
    }
    private val bound = activity.bindService(
        Intent(activity, ProjectionCaptureService::class.java), connection, Context.BIND_AUTO_CREATE,
    )

    init {
        update(if (bound) "화면 공유 서비스 연결 중" else "화면 공유 서비스에 연결할 수 없어요.")
        handler.post(poll)
    }

    override fun requestApproval() {
        if (controller == null) return
        val request = ProjectionCaptureService.approvalIntent(activity, wholeDisplay = true)
        @Suppress("DEPRECATION")
        activity.startActivityForResult(request, CAPTURE_REQUEST_CODE)
    }

    override fun acceptApproval(resultCode: Int, data: Intent?) {
        val result = controller?.open(
            HELP, helpActive = true, consentGranted = true,
            approval = ProjectionCaptureService.approvalFromResult(resultCode, data),
            diagnostic = diagnostic(),
            onSystemStopped = { update(resultMessage(it)) },
            onInvalidated = { if (frames?.isClosed == true) frames = null },
        ) ?: return
        result.data?.frame_source?.let {
            frames = it
            frameCount = 0
        }
        update(resultMessage(result))
    }

    override fun stop() {
        val epoch = frames?.epoch ?: return
        val result = controller?.stop(HELP, epoch, diagnostic()) ?: return
        update(resultMessage(result))
    }

    private fun diagnostic(): Diagnostic {
        val run = ++sequence
        return Diagnostic(
            "avd-debug", "avd-debug-$run", "N-07", Source.FIXTURE,
            Basis("debug-pair", null, HELP, run, 1, null, 1, null, frames?.epoch, null, null),
            emptyList(), 0, emptyMap(), null,
        )
    }

    private fun resultMessage(result: NodeResult<ProjectionData>): String =
        result.outcome ?: result.diagnostics.terminal_error?.code ?: "화면 공유 상태 확인 필요"

    private fun update(message: String) {
        status = message
        onStatus("$message\n활성 epoch ${frames?.epoch ?: "없음"}\n받은 프레임 $frameCount")
    }

    override fun close() {
        handler.removeCallbacks(poll)
        controller?.revokeConsent()
        if (bound) activity.unbindService(connection)
        controller = null
        frames = null
    }

    private companion object {
        const val HELP = "avd-debug"
    }
}
