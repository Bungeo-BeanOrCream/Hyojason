package com.hyojason.app.rtc.debug

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import com.hyojason.app.rtc.CallService
import com.hyojason.app.rtc.endCall
import com.hyojason.app.rtc.fetchRtcToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * AVD 통화 검증용 임시 화면(debug 전용). 두 방법으로 연다.
 *  - 1단계 meet 링크 그대로: `-d '<링크>'` (쿼리의 liveKitUrl·token 사용)
 *  - 서버에서 토큰 받기: `--es server http://10.0.2.2:8000 --es actor <id> --es pair <pair_id> --es call <call_id>`
 *    (끊기가 서버에 HANGUP으로 남는 건 이 방법뿐. 링크로 열면 call 정보가 없어 방만 나간다 → RTC_FAILED)
 */
class RtcTestActivity : Activity() {
    private val scope: CoroutineScope = MainScope()
    private lateinit var statusView: TextView
    private var server: String? = null
    private var actor: String? = null
    private var pair: String? = null
    private var call: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            fitsSystemWindows = true
            setPadding(48, 48, 48, 48)
        }
        statusView = TextView(this).apply { textSize = 17f }
        content.addView(TextView(this).apply { text = "RTC 검증"; textSize = 24f })
        content.addView(statusView)
        content.addView(Button(this).apply { text = "끊기"; setOnClickListener { hangUp() } })
        // D의 지시 이벤트가 붙기 전까지 손으로 누른다. 두 번째부터는 아무 변화 없어야 한다.
        content.addView(Button(this).apply {
            text = "첫 자녀 지시 (테스트)"
            setOnClickListener { CallService.onFirstChildInstruction() }
        })
        setContentView(content)
        scope.launch { CallService.status.collect { statusView.text = it } }

        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) join()
        // 알림은 '통화 중' 표시용, Bluetooth는 무선 이어폰 출력용. 둘 다 거절해도 통화는 된다. 마이크가 0번.
        else requestPermissions(
            arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.BLUETOOTH_CONNECT),
            REQ_MIC,
        )
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode == REQ_MIC && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) join()
        else statusView.text = "마이크 권한이 필요해요"
    }

    private fun join() {
        val link = intent.data
        val url = link?.getQueryParameter("liveKitUrl")
        val token = link?.getQueryParameter("token")
        if (url != null && token != null) return CallService.start(this, url, token)

        server = intent.getStringExtra("server")
        actor = intent.getStringExtra("actor")
        pair = intent.getStringExtra("pair")
        call = intent.getStringExtra("call")
        val server = server
        val actor = actor
        val pair = pair
        val call = call
        if (server == null || actor == null || pair == null || call == null) {
            statusView.text = "링크(-d) 또는 server/actor/pair/call 값이 필요해요"
            return
        }
        scope.launch {
            try {
                val t = withContext(Dispatchers.IO) { fetchRtcToken(server, actor, pair, call) }
                CallService.start(this@RtcTestActivity, t.url, t.token)
            } catch (e: Exception) {
                statusView.text = "토큰 발급 실패: ${e.message}"
            }
        }
    }

    /** 서버에 먼저 종료를 알리고(HANGUP) 방을 나간다. 서버가 안 받아도 방은 나간다 → 서버는 RTC_FAILED로 끝낸다. */
    private fun hangUp() {
        val (s, a, p, c) = listOf(server, actor, pair, call)
        if (s == null || a == null || p == null || c == null) return CallService.stop(this)
        scope.launch {
            runCatching { withContext(Dispatchers.IO) { endCall(s, a, p, c) } }
                .onFailure { statusView.text = "서버 종료 알림 실패: ${it.message}" }
            CallService.stop(this@RtcTestActivity)
        }
    }

    override fun onDestroy() {
        // 화면이 닫혀도 통화는 서비스가 유지한다. 끊기는 버튼으로만.
        scope.cancel()
        super.onDestroy()
    }

    private companion object {
        const val REQ_MIC = 1
    }
}
