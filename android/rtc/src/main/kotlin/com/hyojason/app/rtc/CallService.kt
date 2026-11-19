package com.hyojason.app.rtc

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import com.twilio.audioswitch.AudioDevice
import io.livekit.android.LiveKit
import io.livekit.android.events.RoomEvent
import io.livekit.android.events.collect
import io.livekit.android.room.Room
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

data class AudioControls(
    val connected: Boolean = false,
    val speakerAvailable: Boolean = false,
    val speaker: Boolean = false,
    val muted: Boolean = false,
)

/**
 * 통화 중 LiveKit 방을 들고 있는 마이크 foreground service.
 * 앱이 Settings 등으로 나가도 마이크와 방 연결이 유지되게 하는 것이 목적이다(N-03).
 * 출력은 [CALL_OUTPUTS]로 시작한다. 사용자가 출력을 고르지 않았으면
 * 첫 지시 때 [onFirstChildInstruction]으로 한 번만 스피커 전환을 평가한다.
 * Android 14+는 마이크 FGS를 앱이 보이는 동안 시작해야 하므로 Activity에서 [start]를 부른다.
 */
class CallService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var room: Room? = null
    private var speakerEvaluated = false  // 서비스 하나 = 통화 하나. 통화당 1회
    private var mediaReady = false
    private var endReason: String? = null  // 끊김·연결 실패 문구를 서비스 종료 뒤에도 남긴다

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> stopSelf()
            else -> {
                val url = intent?.getStringExtra(EXTRA_URL)
                val token = intent?.getStringExtra(EXTRA_TOKEN)
                if (url == null || token == null || room != null) return START_NOT_STICKY
                startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
                connect(url, token)
            }
        }
        // ponytail: 프로세스가 죽으면 토큰도 잃으니 재시작하지 않는다. 재접속은 앱이 새 토큰으로.
        return START_NOT_STICKY
    }

    private fun connect(url: String, token: String) {
        val r = LiveKit.create(applicationContext).also { room = it }
        // 접속 전에 정해야 LiveKit이 처음부터 이 순서로 고른다
        r.audioSwitchHandler?.preferredDeviceList = CALL_OUTPUTS
        scope.launch {
            r.events.collect { e ->
                when (e) {
                    is RoomEvent.Disconnected -> { endReason = "끊김"; stopSelf() }
                    is RoomEvent.Reconnecting -> publish("재연결 중")
                    is RoomEvent.Connected, is RoomEvent.Reconnected,
                    is RoomEvent.ParticipantConnected, is RoomEvent.ParticipantDisconnected,
                    is RoomEvent.ActiveSpeakersChanged -> publish(null)
                    else -> Unit
                }
            }
        }
        scope.launch {
            try {
                publish("연결 중")
                r.connect(url, token)
                r.localParticipant.setMicrophoneEnabled(true)
                mediaReady = true
                publish(null)
            } catch (e: Exception) {
                endReason = "연결 실패: ${e.javaClass.simpleName}"
                stopSelf()
            }
        }
    }

    private fun evaluateSpeakerOnce() {
        if (speakerEvaluated) return
        val handler = room?.audioSwitchHandler ?: return  // 아직 접속 전이면 다음 지시에서 다시
        speakerEvaluated = true
        if (switchToSpeakerOnFirstInstruction(handler.selectedAudioDevice?.javaClass)) {
            handler.availableAudioDevices.firstOrNull { it is AudioDevice.Speakerphone }?.let { handler.selectDevice(it) }
        }
        publish(null)
    }

    private fun changeSpeaker() {
        val handler = room?.audioSwitchHandler ?: return
        val current = handler.selectedAudioDevice
        val next = if (current is AudioDevice.Speakerphone) {
            handler.availableAudioDevices.firstOrNull { it !is AudioDevice.Speakerphone }
        } else {
            handler.availableAudioDevices.firstOrNull { it is AudioDevice.Speakerphone }
        } ?: return
        speakerEvaluated = true
        handler.selectDevice(next)
        publish(null)
    }

    private fun changeMute() {
        val participant = room?.localParticipant ?: return
        scope.launch {
            participant.setMicrophoneEnabled(!participant.isMicrophoneEnabled)
            publish(null)
        }
    }

    /** 상태 한 줄. 참가자 identity와 말하는 사람, 출력 장치만 보여 준다. */
    private fun publish(override: String?) {
        val r = room
        val devices = r?.audioSwitchHandler?.availableAudioDevices.orEmpty()
        _controls.value = AudioControls(
            connected = mediaReady && override == null,
            speakerAvailable = mediaReady && override == null && devices.any { it is AudioDevice.Speakerphone } &&
                devices.any { it !is AudioDevice.Speakerphone },
            speaker = r?.audioSwitchHandler?.selectedAudioDevice is AudioDevice.Speakerphone,
            muted = r?.localParticipant?.isMicrophoneEnabled == false,
        )
        _status.value = override ?: if (r == null) "대기" else buildString {
            append("나: ${r.localParticipant.identity?.value} (마이크 ${if (r.localParticipant.isMicrophoneEnabled) "켜짐" else "꺼짐"})\n")
            append("상대: ${r.remoteParticipants.keys.joinToString { it.value }.ifEmpty { "없음" }}\n")
            append("말하는 중: ${r.activeSpeakers.joinToString { it.identity?.value.orEmpty() }.ifEmpty { "-" }}\n")
            append("출력: ${r.audioSwitchHandler?.selectedAudioDevice?.name ?: "-"}")
        }
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        room?.disconnect()
        room?.release()
        room = null
        scope.cancel()
        _status.value = endReason ?: "대기"
        _controls.value = AudioControls()
        super.onDestroy()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "통화", NotificationManager.IMPORTANCE_LOW))
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_phone_call)
            .setContentTitle("효자손 통화 중")
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL = "call"
        private const val NOTIFICATION_ID = 301
        private const val ACTION_STOP = "com.hyojason.app.rtc.STOP"
        private const val EXTRA_URL = "url"
        private const val EXTRA_TOKEN = "token"

        // ponytail: 같은 프로세스 안에서만 부르는 통로. 바인딩 대신 실행 중인 서비스 하나를 잡아 둔다.
        private var instance: CallService? = null

        private val _status = MutableStateFlow("대기")
        val status: StateFlow<String> = _status
        private val _controls = MutableStateFlow(AudioControls())
        val controls: StateFlow<AudioControls> = _controls

        fun toggleSpeaker() { instance?.changeSpeaker() }

        fun toggleMute() { instance?.changeMute() }

        /** RECORD_AUDIO 권한을 받은 뒤, 앱이 화면에 보일 때 부른다. */
        fun start(context: Context, url: String, token: String) {
            context.startForegroundService(
                Intent(context, CallService::class.java).putExtra(EXTRA_URL, url).putExtra(EXTRA_TOKEN, token),
            )
        }

        /** 첫 자녀 지시가 이 기기에 왔을 때 메인 스레드에서 부른다. 두 번째부터는 아무것도 안 한다. */
        fun onFirstChildInstruction() {
            instance?.evaluateSpeakerOnce()
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, CallService::class.java))
        }
    }
}
