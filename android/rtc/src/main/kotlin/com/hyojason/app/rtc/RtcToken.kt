package com.hyojason.app.rtc

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class RtcToken(val url: String, val token: String, val room: String, val identity: String)

/** 서버 응답. 오류일 때 body는 FastAPI의 `{"detail": ...}`. */
data class ServerReply(val code: Int, val body: String) {
    val ok get() = code in 200..299

    /** 오류 코드(예: UNAUTHORIZED, STALE_BASIS). 없으면 "HTTP <code>". */
    val detail: String
        get() = runCatching { JSONObject(body).optString("detail") }.getOrNull()?.takeIf { it.isNotEmpty() }
            ?: "HTTP $code"
}

/**
 * server/voice 호출 하나. 블로킹이라 IO 스레드에서 부른다. 인증은 invite/claim 때 받은 기기 토큰(#10, Bearer).
 * 토큰·본문은 로그에 남기지 않는다.
 */
fun serverRequest(
    serverBaseUrl: String,
    method: String,
    path: String,
    authToken: String?,
    json: JSONObject? = null,
): ServerReply {
    val conn = URL("${serverBaseUrl.trimEnd('/')}$path").openConnection() as HttpURLConnection
    try {
        conn.requestMethod = method
        conn.connectTimeout = 5_000
        conn.readTimeout = 5_000
        authToken?.let { conn.setRequestProperty("Authorization", "Bearer $it") }
        if (json != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(json.toString().toByteArray()) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        return ServerReply(code, stream?.bufferedReader()?.use { it.readText() }.orEmpty())
    } finally {
        conn.disconnect()
    }
}

/** `POST /v1/calls/{call_id}/rtc-token`: 수락된 통화의 참여자에게만 나온다. */
fun fetchRtcToken(serverBaseUrl: String, authToken: String, callId: String): RtcToken {
    val r = serverRequest(serverBaseUrl, "POST", "/v1/calls/$callId/rtc-token", authToken)
    if (!r.ok) throw IllegalStateException("rtc-token ${r.detail}")
    val json = JSONObject(r.body)
    return RtcToken(json.getString("url"), json.getString("token"), json.getString("room"), json.getString("identity"))
}

/**
 * `POST /v1/calls/{call_id}/end`: 끊기 버튼. 방을 나가기 전에 불러야 서버에 HANGUP으로 남는다
 * (그냥 나가면 서버 worker가 퇴장을 보고 RTC_FAILED로 끝낸다). 409(이미 끝남)는 성공으로 본다.
 */
fun endCall(serverBaseUrl: String, authToken: String, callId: String) {
    val r = serverRequest(serverBaseUrl, "POST", "/v1/calls/$callId/end", authToken)
    if (!r.ok && r.code != 409) throw IllegalStateException("end ${r.detail}")
}
