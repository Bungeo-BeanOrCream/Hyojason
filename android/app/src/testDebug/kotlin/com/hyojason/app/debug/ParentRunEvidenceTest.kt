package com.hyojason.app.debug

import com.google.gson.JsonParser
import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.Source
import com.hyojason.app.overlay.gate.GateClock
import com.hyojason.app.overlay.gate.GuideAction
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** 추가 지시 4: D의 N-25 `RunEvidence`·`Basis`와 필드명·null 허용이 같은지 확인한다 (이슈 #5). */
class ParentRunEvidenceTest {
    // server/session/hyojason_session/demo_evaluation.py RunEvidence 필드 (origin/D 기준)
    private val runFields = setOf(
        "case_id", "attempt", "trace_id", "source", "actual", "basis", "screen_source", "model_source",
        "input_source", "device_revision", "app_revision", "profile_revision", "model_revision", "failure_domain",
    )
    // server/session/hyojason_session/common/result.py Basis 필드 11개
    private val basisFields = setOf(
        "pair_id", "call_id", "help_id", "event_seq", "consent_revision", "goal_revision",
        "mode_revision", "instruction_revision", "projection_epoch", "screen_revision", "utterance_seq",
    )

    private val basis = Basis("p1", "c1", "h1", 1, 1, 1, 1, 1, "e1", 2, null)
    private fun run(source: Source = Source.LIVE, screen: Source? = null) =
        ParentRun("E08", 1, "t1", source, basis, screenSource = screen)

    private fun parse(json: String?) = JsonParser.parseString(json!!).asJsonObject.getAsJsonArray("observed_runs")[0].asJsonObject

    @Test fun `행 필드명은 D의 RunEvidence와 같다`() {
        val row = parse(observedLocationJson(run(), true))
        assertEquals(runFields, row.keySet())
        assertEquals(basisFields, row.getAsJsonObject("basis").keySet())
    }

    @Test fun `실측하지 않은 필드는 null로 남고 채워지지 않는다`() {
        val row = parse(observedLocationJson(run(), null))
        for (key in listOf("input_source", "screen_source", "model_source", "device_revision",
            "app_revision", "profile_revision", "model_revision", "failure_domain")) {
            assertTrue(key, row.get(key).isJsonNull)
        }
        assertTrue(row.getAsJsonObject("actual").keySet().isEmpty())
        assertTrue(row.getAsJsonObject("basis").get("utterance_seq").isJsonNull)
    }

    @Test fun `입력 하나가 FIXTURE면 전체 source는 FIXTURE다`() {
        assertEquals("FIXTURE", parse(observedLocationJson(run(Source.LIVE, Source.FIXTURE), true)).get("source").asString)
        assertEquals("LIVE", parse(observedLocationJson(run(Source.LIVE, Source.LIVE), true)).get("source").asString)
    }

    @Test fun `위치 증거는 일치 여부만 내고 좌표·프레임·본문 키가 없다`() {
        val actual = parse(observedLocationJson(run(), true)).getAsJsonObject("actual")
        assertEquals(setOf("bounds_match"), actual.keySet())
        assertTrue(actual.get("bounds_match").asBoolean)
    }

    @Test fun `trace_id와 식별자는 외부 주입값만 받고 URL 같은 값은 거부한다`() {
        val bad = runCatching { ParentRun("E08", 1, "https://x.y/t", Source.LIVE, basis) }
        assertTrue(bad.isFailure)
        assertTrue(runCatching { ParentRun("E08", 0, "t1", Source.LIVE, basis) }.isFailure)
    }

    @Test fun `타이밍은 t0 없이 시작되면 t0_ms를 내지 않고 렌더 전에는 render_ms가 null이다`() {
        var now = 1_000L
        val timing = ParentTiming(GateClock(), HelpMode.CALL_CHILD_LED) { now }
        val before = timing.actual()
        assertFalse(before.containsKey("t0_ms"))
        assertEquals("instruction_received", before["origin"])
        assertEquals(0, before["guide_count"])
        assertNull(before["render_ms"])
        now = 4_000L
        timing.rendered(GuideAction.TAP)
        timing.rendered(GuideAction.BACK)
        val after = timing.actual()
        assertEquals(4_000L, after["render_ms"])
        assertEquals("TAP", after["action"])
        assertEquals(2, after["guide_count"])
    }

    @Test fun `통화 후 모드의 기준 시각 이름이 다르다`() {
        val timing = ParentTiming(GateClock(), HelpMode.POST_CALL_AGENT) { 0L }
        assertEquals("post_call_stable_screen", timing.actual()["origin"])
    }

    @Test fun `trace_id가 없으면 내보내지 않고 UNVERIFIED다`() {
        val run = ParentRun("E08", 1, null, Source.LIVE, basis)
        assertEquals("UNVERIFIED", run.status)
        assertNull(observedLocationJson(run, true))
        assertNull(observedRunsJson(run, ParentTiming(GateClock(), HelpMode.CALL_CHILD_LED) { 0L }))
        assertEquals("VERIFIED", run().status)
    }

    @Test fun `trace_id는 도움 종료에서만 지워지고 새 도움에 이어지지 않는다`() {
        val ids = HelpTraceIds()
        assertNull(ids.current("h1"))
        ids.inject("h1", "t1")
        // 통화 종료·네트워크 끊김은 holder에 알리지 않으므로 값이 유지된다
        assertEquals("t1", ids.current("h1"))
        assertNull(ids.current("h2"))
        ids.endHelp("h2")
        assertEquals("t1", ids.current("h1"))
        ids.endHelp("h1")
        assertNull(ids.current("h1"))
        ids.inject("h2", "t2")
        assertEquals("t2", ids.current("h2"))
        assertTrue(runCatching { ids.inject("h3", "https://x.y/t") }.isFailure)
    }
}
