package com.hyojason.app.rtc

import com.twilio.audioswitch.AudioDevice

/**
 * N-03 오디오 출력. 접속할 때는 역할과 상관없이 일반 통화처럼 시작한다:
 * 이어폰·Bluetooth가 있으면 그쪽, 없으면 수화기. 앞에 있을수록 우선.
 */
val CALL_OUTPUTS: List<Class<out AudioDevice>> = listOf(
    AudioDevice.BluetoothHeadset::class.java,
    AudioDevice.WiredHeadset::class.java,
    AudioDevice.Earpiece::class.java,
    AudioDevice.Speakerphone::class.java,
)

/**
 * 첫 자녀 지시가 왔을 때 한 번만 평가한다(N-03). 화면을 보며 들어야 하므로 스피커로 바꾼다.
 * 이어폰·Bluetooth로 듣고 있으면 그대로 둔다. 장치 종류만 보고 판단한다(AudioDevice는 밖에서 못 만든다).
 */
fun switchToSpeakerOnFirstInstruction(selected: Class<out AudioDevice>?): Boolean =
    selected != AudioDevice.BluetoothHeadset::class.java && selected != AudioDevice.WiredHeadset::class.java
