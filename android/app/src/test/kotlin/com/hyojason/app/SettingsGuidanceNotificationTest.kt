package com.hyojason.app

import com.hyojason.app.overlay.gate.GuideAction
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.overlay.gate.LocalGuide
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsGuidanceNotificationTest {
    @Test fun b06_설정_안내는_기기에_보이는_버튼_이름을_사용한다() {
        assertEquals("‘디스플레이 및 터치’ 항목을 눌러 주세요.",
            settingsGuidanceText(LocalGuide(GuideAction.TAP), "settings.display"))
        assertEquals("화면을 아래로 내려 주세요.", settingsGuidanceText(LocalGuide(GuideAction.SCROLL_DOWN), null))
    }

    @Test fun b06_알수없는_버튼과_임의_문구는_알림에_노출하지_않는다() {
        assertNull(settingsGuidanceText(LocalGuide(GuideAction.TAP), "unknown"))
        assertNull(settingsGuidanceText(LocalGuide(null, message = "arbitrary text"), null))
        assertEquals("되돌리기 어려운 동작이에요. 천천히 확인하세요",
            settingsGuidanceText(LocalGuide(null, message = "되돌리기 어려운 동작이에요. 천천히 확인하세요"), null))
    }

    @Test fun b06_오래된_프레임_한번으로는_설정_안내를_지우지_않는다() {
        assertTrue(keepSettingsNoticeOn("CAPTURE_GAP", ObservationHealth.STABLE))
        assertFalse(keepSettingsNoticeOn("CAPTURE_GAP", ObservationHealth.GAP))
        assertFalse(keepSettingsNoticeOn("STALE_BASIS", ObservationHealth.STABLE))
        assertFalse(keepSettingsNoticeOn(null, ObservationHealth.STABLE))
    }
}
