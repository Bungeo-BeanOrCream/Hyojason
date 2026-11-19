package com.hyojason.app.child

import android.content.Context
import com.hyojason.app.rtc.RtcToken
import com.hyojason.app.rtc.ServerReply
import com.hyojason.app.rtc.endCall
import com.hyojason.app.rtc.fetchRtcToken
import com.hyojason.app.rtc.serverRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * 이 기기의 연결 정보(SharedPreferences). 기기 토큰은 처음 invite/claim 때 서버가 준다(#10).
 */
class Store(context: Context) {
    private val p = context.getSharedPreferences("hyojason", Context.MODE_PRIVATE)

    val server: String = BuildConfig.BACKEND_URL
    var token: String?  // 비밀값. 로그·화면에 내지 않는다
        get() = p.getString("token", null)
        set(v) = p.edit().putString("token", v).apply()
    var role: Role
        get() = Role.valueOf(p.getString("role", Role.PARENT.name)!!)
        set(v) = p.edit().putString("role", v.name).apply()
    var pairId: String?
        get() = p.getString("pair_id", null)
        set(v) = p.edit().putString("pair_id", v).apply()
    var paired: Boolean
        get() = p.getBoolean("paired", false)
        set(v) = p.edit().putBoolean("paired", v).apply()
    var consentAsked: Boolean
        get() = p.getBoolean("consent_asked", false)
        set(v) = p.edit().putBoolean("consent_asked", v).apply()

    /** 서버가 재시작돼 페어를 잊었거나 새로 연결할 때. 토큰도 지워 서버가 새 기기로 보게 한다. */
    fun reset() {
        p.edit().clear().apply()
    }
}

/** server/voice 호출. 모두 IO 스레드에서 돈다. */
class Api(private val s: Store) {
    private suspend fun call(method: String, path: String, json: JSONObject? = null): ServerReply =
        withContext(Dispatchers.IO) { serverRequest(s.server, method, path, s.token, json) }

    /** invite/claim 응답에 새 기기 토큰이 있으면 저장한다(처음 한 번). */
    private fun keepToken(j: JSONObject): JSONObject = j.also { if (it.has("token")) s.token = it.getString("token") }

    /** N-01 코드 만들기 → (pair_id, code) */
    suspend fun invite(role: Role): Result<Pair<String, String>> = call(
        "POST", "/v1/pairs/invite", JSONObject().put("role", role.name),
    ).map { keepToken(JSONObject(it)).let { j -> j.getString("pair_id") to j.getString("invite_code") } }

    /** N-01 코드 입력 → pair_id */
    suspend fun claimCode(role: Role, code: String): Result<String> = call(
        "POST", "/v1/pairs/claim", JSONObject().put("role", role.name).put("invite_code", code),
    ).map { keepToken(JSONObject(it)).getJSONObject("data").getString("pair_id") }

    /** N-01 코드를 만든 쪽이 상대 입력을 기다리며 묻는다 → paired 여부 */
    suspend fun isPaired(role: Role, pairId: String): Result<Boolean> = call(
        "POST", "/v1/pairs/claim", JSONObject().put("role", role.name).put("pair_id", pairId),
    ).map { JSONObject(it).getString("outcome") == "paired" }

    suspend fun consentEnabled(): Result<Boolean> =
        call("GET", "/v1/consent").map { JSONObject(it).getBoolean("stt_allowed") }

    /** N-02. 서버 버전을 읽고 바로 쓴다. 그 사이 상대가 바꿨으면(STALE_BASIS) 한 번 더. */
    suspend fun setConsent(enabled: Boolean, caps: List<String>): Result<Unit> {
        repeat(2) {
            val rev = call("GET", "/v1/consent").map { JSONObject(it).getInt("consent_revision") }
                .getOrElse { e -> return Result.failure(e) }
            val body = JSONObject().put("enabled", enabled).put("consent_revision", rev)
                .put("device_capabilities", JSONArray(caps))
            val r = call("POST", "/v1/consent", body)
            if (r.ok) return Result.success(Unit)
            if (r.detail != "STALE_BASIS") return Result.failure(IllegalStateException(r.detail))
        }
        return Result.failure(IllegalStateException("STALE_BASIS"))
    }

    suspend fun startCall(assistedRole: Role): Result<String> =
        call("POST", "/v1/calls", JSONObject().put("assisted_role", assistedRole.name))
            .map { JSONObject(it).getJSONObject("data").getString("call_id") }

    suspend fun accept(callId: String): Result<Unit> = call("POST", "/v1/calls/$callId/accept").map { }

    /** 취소·거절·끊기 모두 같은 종료(HANGUP). 이미 끝났어도 성공. */
    suspend fun end(callId: String): Result<Unit> =
        runCatching { withContext(Dispatchers.IO) { endCall(s.server, s.token!!, callId) } }

    suspend fun token(callId: String): Result<RtcToken> =
        runCatching { withContext(Dispatchers.IO) { fetchRtcToken(s.server, s.token!!, callId) } }

    /** 진행 중 통화. 없으면 null. 페어를 서버가 모르면(재시작 등) UNAUTHORIZED 실패. */
    /** 가족 연결 끊기. 진행 중 통화도 서버가 끝낸다. 상대 기기는 다음 폴링에서 UNAUTHORIZED를 받고 연결 화면으로. */
    suspend fun unpair(): Result<Unit> = call("POST", "/v1/pairs/unpair").map { }

    suspend fun current(): Result<CurrentCall?> = call("GET", "/v1/calls/current").map { body ->
        if (body.isBlank() || body == "null") return@map null
        val j = JSONObject(body)
        CurrentCall(j.getString("call_id"), Role.valueOf(j.getString("caller_role")), j.getBoolean("accepted"))
    }

    private inline fun <T> ServerReply.map(f: (String) -> T): Result<T> =
        if (ok) runCatching { f(body) } else Result.failure(IllegalStateException(detail))
}
