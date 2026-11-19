package com.hyojason.app.child

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.graphics.Typeface
import android.content.res.ColorStateList
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.widget.ProgressBar
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import com.hyojason.app.rtc.CallService
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 부모·자녀 공용 앱 화면: 역할 토글·가족 연결(N-01) → AI 동의(N-02) → 홈·발신·수신·통화(N-03).
 *
 * 서버 상태는 화면이 보이는 동안 1.5초마다 묻는다(수신·수락·상대 끊음).
 * ponytail: 폴링. D의 WS /v1/events가 생기면 [poll]만 이벤트 수신으로 바꾼다.
 * 이번 범위 밖: 안내 끄기(help.stop, D)·안내 표시(A)·도움받는 사람 변경(#6 결정 대기).
 */
open class HomeActivity : Activity() {
    private val scope = MainScope()
    private lateinit var store: Store
    private lateinit var api: Api
    private lateinit var root: LinearLayout

    private var screen: Screen? = null
    private var current: CurrentCall? = null
    private var shownCode: String? = null  // 코드 만들기 후 상대 입력을 기다리는 중
    private var codeDeadline = 0L
    private var codeEntry = ""
    private var entering = false  // 코드 입력 화면
    private var message: String? = null  // 화면 아래 한 줄 안내(오류 등)
    private var consentOn: Boolean? = null
    private var joinedCall: String? = null  // CallService를 띄운 call_id
    private var callStartedAt = 0L
    private var pendingAfterMic: (() -> Unit)? = null
    private var pollJob: Job? = null
    private var tickJob: Job? = null
    private var assistedRole = Role.PARENT

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        api = Api(store)
        window.setDecorFitsSystemWindows(false)
        val lightBars = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or
            WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Palette.BG)
            val pad = (24 * resources.displayMetrics.density).toInt()
            setPadding(pad, pad, pad, pad)
        }
        setContentView(ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(Palette.BG)
            setOnApplyWindowInsetsListener { view, insets ->
                val safe = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
                view.setPadding(safe.left, safe.top, safe.right, safe.bottom)
                insets
            }
            addView(root)
        })
        window.decorView.windowInsetsController?.setSystemBarsAppearance(lightBars, lightBars)
        render(localScreen() ?: Screen.HOME)
    }

    override fun onStart() {
        super.onStart()
        pollJob = scope.launch { while (isActive) { poll(); delay(1_500) } }
    }

    override fun onStop() {
        pollJob?.cancel()  // 통화는 CallService가 유지한다
        super.onStop()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** 서버를 묻지 않고 정해지는 화면. null이면 서버의 통화 상태로 정한다. */
    private fun localScreen(): Screen? = when {
        !store.paired -> Screen.CONNECT
        !store.consentAsked -> Screen.CONSENT
        else -> null
    }

    private suspend fun poll() {
        if (!store.paired) {
            val pid = store.pairId ?: return
            if (shownCode == null) return
            api.isPaired(store.role, pid).onSuccess { if (it) onPaired(pid) }
            return
        }
        // 상대가 나중에 동의·철회해도 홈 표시가 따라오게 매번 읽는다. ponytail: WS /v1/events가 생기면 consent 이벤트로
        if (screen == Screen.HOME || consentOn == null) api.consentEnabled().onSuccess {
            if (it != consentOn) { consentOn = it; if (screen == Screen.HOME) redraw() }
        }
        localScreen()?.let { return render(it) }
        api.current()
            .onSuccess { c ->
                current = c
                val next = callScreen(store.role, c)
                if (next == Screen.IN_CALL) join(c!!.callId)
                if (next != Screen.IN_CALL && joinedCall != null) leave()
                render(next)
            }
            .onFailure { e ->
                if (e.message == "UNAUTHORIZED") {  // 서버가 재시작돼 페어를 잊었다(메모리 저장)
                    store.reset()
                    store = Store(this); api = Api(store)
                    message = if (takeHelpCompletionNotice()) "이번 도움을 마쳤어요. 다음 도움은 새 번호로 연결해 주세요."
                    else "연결 정보가 사라졌어요. 다시 연결해 주세요."
                    render(Screen.CONNECT, force = true)
                }
                // 그 밖의 네트워크 오류는 다음 주기에 다시 묻는다
            }
    }

    private fun render(next: Screen, force: Boolean = false) {
        if (next == screen && !force) return  // 같은 화면이면 다시 그리지 않는다(입력 중인 글자 보존)
        if (next == Screen.IN_CALL && screen != Screen.IN_CALL) callStartedAt = SystemClock.elapsedRealtime()
        screen = next
        redraw()
    }

    private fun redraw() {
        tickJob?.cancel()
        root.removeAllViews()
        root.brand()
        when (screen) {
            Screen.CONNECT -> if (shownCode != null) drawShowCode() else if (entering) drawEnterCode() else drawConnect()
            Screen.CONSENT -> drawConsent()
            Screen.HOME, null -> drawHome()
            Screen.OUTGOING -> drawOutgoing()
            Screen.INCOMING -> drawIncoming()
            Screen.IN_CALL -> drawInCall()
        }
        drawAssistanceControls(root, store.paired, screen == Screen.IN_CALL)
        message?.let { root.body(it, 16f, Palette.RED) }
    }

    protected open fun drawAssistanceControls(root: LinearLayout, paired: Boolean, inCall: Boolean) = Unit
    protected open fun takeHelpCompletionNotice(): Boolean = false

    private fun say(text: String?) {
        message = text
        redraw()
    }

    // ---- N-01 가족 연결 ----

    private fun drawConnect() {
        root.title("가족과 연결하기")
        root.body("도움을 주고받을 가족과 연결해요.")
        root.body("나는 누구인가요?", 17f, Palette.INK)
        root.toggle("부모님", "자녀", store.role == Role.PARENT) { left ->
            store.role = if (left) Role.PARENT else Role.CHILD
            redraw()
        }
        root.card(Palette.CREAM) {
            body("내 기기에서 새 번호를 발급해요.", 16f)
            primary("코드 만들기") { makeCode() }
        }
        root.card(Palette.MIST) {
            body("가족에게 받은 번호를 넣어요.", 16f)
            secondary("코드 입력") {
                // An unfinished invite may still own the old token on the server.
                store.pairId = null
                store.token = null
                entering = true
                codeEntry = ""
                say(null)
            }
        }
    }

    private fun makeCode() = scope.launch {
        api.invite(store.role)
            .onSuccess { (pid, code) ->
                store.pairId = pid
                shownCode = code
                codeDeadline = SystemClock.elapsedRealtime() + CODE_LIFETIME_MS
                say(null)
            }
            .onFailure { say("번호를 만들지 못했어요. 잠시 뒤 다시 시도해 주세요.") }
    }

    private fun drawShowCode() {
        val code = shownCode ?: return
        root.title("이 번호를 알려 주세요")
        root.body("상대 기기에서 ‘코드 입력’을 눌러 아래 번호를 입력해 주세요.")
        root.digits(code)
        val remaining = root.body("", 16f, Palette.BLUE).apply { typeface = Typeface.MONOSPACE }
        val progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = (CODE_LIFETIME_MS / 1000).toInt()
            progressTintList = ColorStateList.valueOf(Palette.ORANGE)
            progressBackgroundTintList = ColorStateList.valueOf(Palette.SKY)
            importantForAccessibility = android.view.View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }
        root.addView(progress, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(8)))
        root.chip("상대 입력 대기")
        val copy = root.secondary("번호 복사") {
            getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("효자손 연결 코드", code))
        }
        root.secondary("새 번호 만들기") { makeCode() }
        root.secondary("취소") {
            shownCode = null
            store.pairId = null
            store.token = null
            say(null)
        }
        tickJob = scope.launch {
            while (isActive) {
                val seconds = ((codeDeadline - SystemClock.elapsedRealtime() + 999) / 1000).coerceIn(0, progress.max.toLong())
                progress.progress = seconds.toInt()
                remaining.text = if (seconds > 0) "남은 시간 ${elapsed(seconds)}" else "번호가 만료됐어요"
                copy.isEnabled = seconds > 0
                delay(1_000)
            }
        }
    }

    private fun drawEnterCode() {
        root.title("받은 번호를 입력해요")
        root.body("${store.role.other.label}이 만든 6자리 번호예요.")
        val input = root.codeInput()
        input.setText(codeEntry)
        root.spacer()
        root.primary("연결하기") {
            val code = input.text.toString()
            codeEntry = code
            if (code.length != 6) return@primary say("6자리를 모두 입력해 주세요")
            scope.launch {
                api.claimCode(store.role, code)
                    .onSuccess { onPaired(it) }
                    // 만료·재사용·같은 역할을 서버가 구분해 주지 않는다(상대 정보 보호)
                    .onFailure { say("연결하지 못했어요. 번호와 '나는 누구인가요' 선택을 확인해 주세요.") }
            }
        }
        root.secondary("뒤로") { entering = false; say(null) }
    }

    private fun onPaired(pairId: String) {
        store.pairId = pairId
        store.paired = true
        shownCode = null
        entering = false
        codeEntry = ""
        message = null
        render(Screen.CONSENT, force = true)
    }

    // ---- N-02 AI 동의 ----

    private fun drawConsent() {
        root.title("AI 안내를 쓸까요?")
        root.body("두 사람이 동의하면 이번 통화에서 화면 안내를 사용할 수 있어요.")
        root.card(Palette.SKY) {
            body("통화 음성", 17f, Palette.INK)
            body("도와주는 사람이 말한 지시를 글자로 바꿔 확인해요. 녹음 파일은 남기지 않아요.", 16f)
            body("화면 안내", 17f, Palette.INK)
            body("도움받는 기기가 권한을 허용하면 누를 곳을 짚어 드려요.", 16f)
        }
        root.spacer()
        root.primary("동의해요") { withMic { saveConsent(true) } }
        root.secondary("동의 안 함 (전화만)") { saveConsent(false) }
    }

    private fun saveConsent(enabled: Boolean) = scope.launch {
        api.setConsent(enabled, capabilities())
            .onSuccess { store.consentAsked = true; consentOn = null; message = null; render(Screen.HOME, force = true) }
            .onFailure { say("저장하지 못했어요 (${it.message})") }
    }

    /**
     * 이 기기가 지금 가진 권한(N-02 device_capabilities). 서버는 부모 기기 값만 캡처·표시 판단에 쓴다.
     * ponytail: CAPTURE는 화면 캡처 승인이 통화마다 새로 받는 것이라 여기서 알 수 없다 → A(observer) 연결 때 보고.
     */
    private fun capabilities(): List<String> = buildList {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) add("MIC")
        if (Settings.canDrawOverlays(this@HomeActivity)) add("OVERLAY")
    }

    // ---- N-03 홈·발신·수신·통화 ----

    private fun drawHome() {
        val peer = store.role.other.label
        root.chip("가족 연결 완료")
        root.title("이번 도움")
        root.card(Palette.CREAM) {
            body("도움받는 사람", 16f, Palette.MUTED)
            body(assistedRole.label, 24f, Palette.INK).typeface = Typeface.DEFAULT_BOLD
            secondary("도움받는 사람 변경") {
                AlertDialog.Builder(this@HomeActivity)
                    .setTitle("누가 도움받나요?")
                    .setSingleChoiceItems(arrayOf("부모님", "자녀"), if (assistedRole == Role.PARENT) 0 else 1) { dialog, index ->
                        assistedRole = if (index == 0) Role.PARENT else Role.CHILD
                        dialog.dismiss()
                        redraw()
                    }
                    .setNegativeButton("취소", null)
                    .show()
            }
        }
        root.card {
            body("AI 안내 · ${when (consentOn) { true -> "켜짐"; false -> "꺼짐 (전화만)"; null -> "확인 중" }}", 18f, Palette.INK)
            if (consentOn == true) body("상대가 동의하지 않았다면 전화만 돼요.", 15f)
            secondary(if (consentOn == true) "AI 안내 끄기" else "AI 안내 켜기") {
                val turnOn = consentOn != true
                if (turnOn) withMic { saveConsent(true) } else saveConsent(false)
            }
        }
        root.spacer()
        root.primary("${peer}에게 전화") { withMic { dial() } }
        root.secondary("가족 연결 끊기") { confirmUnpair(peer) }
    }

    /** 통화가 끝나도 가족 연결은 남는다. 끊는 건 이 버튼뿐이라 한 번 더 묻는다. 서버가 못 받아도 이 기기는 끊는다. */
    private fun confirmUnpair(peer: String) {
        AlertDialog.Builder(this)
            .setMessage("${peer} 기기와의 가족 연결을 끊을까요? 다시 쓰려면 새 번호로 연결해야 해요.")
            .setPositiveButton("끊기") { _, _ ->
                scope.launch {
                    api.unpair()
                    leave(); current = null
                    store.reset(); store = Store(this@HomeActivity); api = Api(store)
                    say(null); render(Screen.CONNECT, force = true)
                }
            }
            .setNegativeButton("취소", null)
            .show()
    }

    private fun dial() = scope.launch {
        api.startCall(assistedRole)
            .onSuccess { cid -> current = CurrentCall(cid, store.role, false); message = null; render(Screen.OUTGOING) }
            .onFailure { say(if (it.message == "DUPLICATE") "이미 진행 중인 전화가 있어요" else "전화를 걸지 못했어요 (${it.message})") }
    }

    private fun drawOutgoing() {
        root.chip("거는 중")
        root.card(Palette.MIST) {
            title("${store.role.other.label}에게 거는 중")
            body("상대가 받으면 연결돼요.")
        }
        root.spacer()
        root.primary("취소", Palette.RED) { hangUp() }
    }

    private fun drawIncoming() {
        root.chip("전화 왔어요")
        root.card(Palette.MIST) { title("${store.role.other.label}에게서 전화") }
        root.spacer()
        root.primary("받기") {
            val cid = current?.callId ?: return@primary
            withMic {
                scope.launch {
                    api.accept(cid).onSuccess { join(cid); render(Screen.IN_CALL) }
                        .onFailure { say("받지 못했어요 (${it.message})") }
                }
            }
        }
        root.primary("거절", Palette.RED) { hangUp() }
    }

    private fun drawInCall() {
        root.chip("통화 중")
        root.title("${store.role.other.label}와 통화 중")
        lateinit var timer: TextView
        root.card(Palette.SKY) {
            body("통화 시간", 16f, Palette.BLUE)
            timer = body("00:00", 32f, Palette.INK).apply { typeface = Typeface.MONOSPACE }
        }
        val audio = root.card {
            body("오디오", 16f, Palette.BLUE)
        }
        lateinit var speaker: android.widget.Button
        lateinit var mute: android.widget.Button
        audio.controls {
            speaker = secondary("스피커") { CallService.toggleSpeaker() }
            mute = secondary("음소거") { CallService.toggleMute() }
        }
        tickJob = scope.launch {
            launch {
                CallService.controls.collect { state ->
                    speaker.isEnabled = state.speakerAvailable
                    speaker.text = if (state.speaker) "스피커 끄기" else "스피커 켜기"
                    mute.isEnabled = state.connected
                    mute.text = if (state.muted) "음소거 해제" else "음소거"
                }
            }
            while (isActive) {
                timer.text = elapsed((SystemClock.elapsedRealtime() - callStartedAt) / 1000)
                delay(1_000)
            }
        }
        root.spacer()
        root.primary("통화 끊기", Palette.RED) { hangUp() }
    }

    /** 수락된 통화에 한 번만 들어간다. 마이크 FGS는 화면이 보이는 지금 시작해야 한다(Android 14+). */
    private fun join(callId: String) {
        if (joinedCall == callId) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            return withMic { join(callId) }
        }
        joinedCall = callId
        scope.launch {
            api.token(callId)
                .onSuccess { CallService.start(this@HomeActivity, it.url, it.token) }
                .onFailure { joinedCall = null; say("통화에 연결하지 못했어요 (${it.message})") }
        }
    }

    private fun leave() {
        joinedCall = null
        CallService.stop(this)
    }

    /** 취소·거절·끊기: 서버에 먼저 알리고(HANGUP) 방을 나간다. 서버가 못 받아도 방은 나간다. */
    private fun hangUp() {
        val cid = current?.callId
        scope.launch {
            if (cid != null) api.end(cid)
            leave()
            current = null
            message = null
            render(Screen.HOME, force = true)
        }
    }

    // ---- 권한 ----

    private fun withMic(then: () -> Unit) {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) return then()
        pendingAfterMic = then
        // 알림은 '통화 중' 표시, Bluetooth는 무선 이어폰 출력용. 거절해도 통화는 된다. 마이크가 0번.
        requestPermissions(
            arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.BLUETOOTH_CONNECT),
            REQ_MIC,
        )
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (requestCode != REQ_MIC) return
        val next = pendingAfterMic
        pendingAfterMic = null
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) next?.invoke()
        else say("전화하려면 마이크 권한이 필요해요")
    }

    private companion object {
        const val REQ_MIC = 7
        const val CODE_LIFETIME_MS = 600_000L
    }
}
