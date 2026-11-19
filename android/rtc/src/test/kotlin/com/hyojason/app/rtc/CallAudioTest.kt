package com.hyojason.app.rtc

import com.twilio.audioswitch.AudioDevice
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CallAudioTest {
    @Test
    fun call_starts_on_headset_then_earpiece() {
        assertEquals(AudioDevice.BluetoothHeadset::class.java, CALL_OUTPUTS[0])
        assertEquals(AudioDevice.WiredHeadset::class.java, CALL_OUTPUTS[1])
        assertEquals(AudioDevice.Earpiece::class.java, CALL_OUTPUTS[2])
    }

    @Test
    fun first_instruction_switches_earpiece_to_speaker() {
        assertTrue(switchToSpeakerOnFirstInstruction(AudioDevice.Earpiece::class.java))
        assertTrue(switchToSpeakerOnFirstInstruction(null))  // 아직 선택 전
    }

    @Test
    fun first_instruction_keeps_headset_or_bluetooth() {
        assertFalse(switchToSpeakerOnFirstInstruction(AudioDevice.WiredHeadset::class.java))
        assertFalse(switchToSpeakerOnFirstInstruction(AudioDevice.BluetoothHeadset::class.java))
    }
}
