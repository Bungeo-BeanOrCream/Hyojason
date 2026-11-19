package com.hyojason.app.rtc

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class RtcToken(val url: String, val token: String, val room: String, val identity: String)

/**
 * `POST /v1/calls/{call_id}/rtc-token` (server/voice). 블로킹 호출이라 IO 스레드에서 부른다.
 * 인증은 아직 dev 헤더(X-Actor-Id/X-Pair-Id)뿐이다.
 */
fun fetchRtcToken(serverBaseUrl: String, actorId: String, pairId: String, callId: String): RtcToken {
    val (code, body) = post(serverBaseUrl, "/v1/calls/$callId/rtc-token", actorId, pairId)
    // 토큰·본문은 로그에 남기지 않는다. 상태 코드만 올린다.
    if (code != 200) throw IllegalStateException("rtc-token HTTP $code")
    val json = JSONObject(body)
    return RtcToken(json.getString("url"), json.getString("token"), json.getString("room"), json.getString("identity"))
}

/**
 * `POST /v1/calls/{call_id}/end`: 끊기 버튼. 방을 나가기 전에 불러야 서버에 HANGUP으로 남는다
 * (그냥 나가면 서버 worker가 퇴장을 보고 RTC_FAILED로 끝낸다). 409(이미 끝남)는 성공으로 본다.
 */
fun endCall(serverBaseUrl: String, actorId: String, pairId: String, callId: String) {
    val (code, _) = post(serverBaseUrl, "/v1/calls/$callId/end", actorId, pairId)
    if (code != 200 && code != 409) throw IllegalStateException("end HTTP $code")
}

private fun post(serverBaseUrl: String, path: String, actorId: String, pairId: String): Pair<Int, String> {
    val conn = URL("${serverBaseUrl.trimEnd('/')}$path").openConnection() as HttpURLConnection
    try {
        conn.requestMethod = "POST"
        conn.connectTimeout = 5_000
        conn.readTimeout = 5_000
        conn.setRequestProperty("X-Actor-Id", actorId)
        conn.setRequestProperty("X-Pair-Id", pairId)
        val code = conn.responseCode
        val body = if (code == 200) conn.inputStream.bufferedReader().use { it.readText() } else ""
        return code to body
    } finally {
        conn.disconnect()
    }
}
