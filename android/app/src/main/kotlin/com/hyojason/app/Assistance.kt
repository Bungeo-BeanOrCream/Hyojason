package com.hyojason.app

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import com.hyojason.app.child.Store
import com.hyojason.app.child.Palette
import com.hyojason.app.child.body
import com.hyojason.app.child.card
import com.hyojason.app.child.secondary
import com.hyojason.app.contract.Basis
import com.hyojason.app.contract.Diagnostic
import com.hyojason.app.contract.HelpMode
import com.hyojason.app.contract.Source
import com.hyojason.app.observer.LocalObservation
import com.hyojason.app.observer.LocalRect
import com.hyojason.app.observer.ObservationFlow
import com.hyojason.app.observer.ObservationHealth
import com.hyojason.app.observer.ScreenState
import com.hyojason.app.observer.detection.CaptureFrame
import com.hyojason.app.observer.detection.DemoProfiles
import com.hyojason.app.observer.detection.DetectionProfile
import com.hyojason.app.observer.detection.FeatureKey
import com.hyojason.app.observer.detection.FeatureKind
import com.hyojason.app.observer.detection.FeatureScan
import com.hyojason.app.observer.detection.FrameSafety
import com.hyojason.app.observer.detection.KoreanTextScan
import com.hyojason.app.observer.detection.ProfileScreen
import com.hyojason.app.observer.detection.ProfileVersion
import com.hyojason.app.observer.detection.ScreenDetection
import com.hyojason.app.observer.detection.TargetRule
import com.hyojason.app.observer.projection.ProjectionCaptureService
import com.hyojason.app.observer.projection.ProjectionFrames
import com.hyojason.app.overlay.gate.CurrentTarget
import com.hyojason.app.overlay.gate.GateInput
import com.hyojason.app.overlay.gate.GuideAction
import com.hyojason.app.overlay.gate.GuideGate
import com.hyojason.app.overlay.gate.InputEvidence
import com.hyojason.app.overlay.gate.LocalGuide
import com.hyojason.app.overlay.gate.ObservedVerdict
import com.hyojason.app.overlay.gate.ParentGuideFlow
import com.hyojason.app.overlay.gate.PostCallProposal
import com.hyojason.app.overlay.gate.Scoped
import com.hyojason.app.overlay.gate.TargetLocation
import com.hyojason.app.overlay.gate.Verdict
import com.hyojason.app.overlay.gate.VerifiedRecovery
import com.hyojason.app.overlay.surface.AndroidSurfaceHost
import com.hyojason.app.overlay.surface.SurfaceUserEvent
import com.hyojason.app.rtc.serverRequest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Owns the assistance channel and local screen capture after the call UI moves to the background. */
internal class Assistance(private val activity: MvpActivity) : AutoCloseable {
    private val scope = MainScope()
    private val store = Store(activity)
    private val http = OkHttpClient.Builder().pingInterval(15, TimeUnit.SECONDS).build()
    private val profile = DemoProfiles.fixture.let { fixture ->
        // EXPERIMENT (#37, A review needed): the Pixel launcher home allows third-party overlays, so it
        // exercises the real red-border path. Its labels were read on the Korean API 35 AVD.
        val screens = fixture.screens.filter {
            it.kind.startsWith("settings_") || it.kind == "home"
        }.map { screen ->
            val label = FeatureKey(FeatureKind.TEXT, "디스플레이 및 터치")
            when (screen.kind) {
                "settings_home" -> {
                    val target = screen.targets.getValue("settings.display")
                    val search = FeatureKey(FeatureKind.TEXT, "설정 검색")
                    val context = FeatureKey(FeatureKind.TEXT, "네트워크 및 인터넷")
                    val sound = FeatureKey(FeatureKind.TEXT, "소리 및 진동")
                    val wallpaper = FeatureKey(FeatureKind.TEXT, "배경화면 및 스타일")
                    // Rows above/below "디스플레이 및 터치" in the Korean API 35 list, used only on this device
                    // to tell that the target is below the fold. Probe ids never leave the device.
                    val probes = (SETTINGS_ROWS_ABOVE_DISPLAY.mapIndexed { i, row -> "$PROBE_ABOVE$i" to row } +
                        SETTINGS_ROWS_BELOW_DISPLAY.mapIndexed { i, row -> "$PROBE_BELOW$i" to row })
                        .associate { (id, row) -> id to TargetRule(setOf(FeatureKey(FeatureKind.TEXT, row))) }
                    screen.copy(anchors = screen.anchors + listOf(setOf(search, context), setOf(search, sound, label),
                        setOf(sound, label, wallpaper)),
                        targets = screen.targets + probes + ("settings.display" to target.copy(aliases = target.aliases + label)))
                }
                // The app list (swipe up from home) is treated as the same launcher screen, like B's tool:
                // an app missing on home is found there. "캘린더"+"주소록" appear only in the app list.
                "home" -> screen.copy(anchors = listOf(
                    setOf(FeatureKey(FeatureKind.TEXT, "Play 스토어"), FeatureKey(FeatureKind.TEXT, "Chrome")),
                    setOf(FeatureKey(FeatureKind.TEXT, "Gmail"), FeatureKey(FeatureKind.TEXT, "YouTube")),
                    setOf(FeatureKey(FeatureKind.TEXT, "캘린더"), FeatureKey(FeatureKind.TEXT, "주소록"))),
                    targets = screen.targets + ("launcher.maps" to TargetRule(setOf(
                        FeatureKey(FeatureKind.TEXT, "지도"), FeatureKey(FeatureKind.TEXT, "Maps")))))
                "settings_display" -> screen.copy(anchors = screen.anchors + listOf(
                    setOf(label, FeatureKey(FeatureKind.TEXT, "밝기 수준"))))
                else -> screen
            }
        }
        // EXPERIMENT (#37): Google app home. It does not hide overlays, so the red border is visible.
        // Labels read on the Korean API 35 AVD; "검색" is not used because it appears twice.
        fun text(value: String) = FeatureKey(FeatureKind.TEXT, value)
        val google = ProfileScreen("google_home", listOf(setOf(text("사진 검색"), text("텍스트 번역"))), mapOf(
            "google.photo_search" to TargetRule(setOf(text("사진 검색"))),
            "google.translate" to TargetRule(setOf(text("텍스트 번역"))),
            "google.saved" to TargetRule(setOf(text("저장됨"))),
        ))
        DetectionProfile("settings-ko-api35", 1, ProfileVersion("15", "sdk_gphone64_arm64"), screens + google)
    }
    private val deviceProfileVersion by lazy {
        if (Build.VERSION.SDK_INT != 35 || Locale.getDefault().language != "ko") null
        else runCatching {
            val settings = activity.packageManager.getPackageInfo("com.android.settings", PackageManager.PackageInfoFlags.of(0))
            settings.versionName?.let { ProfileVersion(it, Build.PRODUCT) }
        }.getOrNull()
    }
    private val scanner = lazy { KoreanTextScan(maxWidth = 720) }
    private var socket: WebSocket? = null
    private var token: String? = null
    private var connected = false
    private var paired = false
    private var callId: String? = null
    private var helpId: String? = null
    private var traceId: String? = null
    private var mode = HelpMode.CALL_CHILD_LED
    private var instruction: Long? = null
    private var t0: Long? = null
    private var decision: JSONObject? = null
    private var decisionAt = 0L
    private var controller: ProjectionCaptureService.Controller? = null
    private var approvalBasis: Basis? = null
    private var frames: ProjectionFrames? = null
    private var flow: ObservationFlow? = null
    private var local: LocalObservation? = null
    private var published: ScreenState? = null
    private var lastScreenRevision = 0L
    private var awaitingVerdict = false
    private var lastStableKind: String? = null
    private var receivesHelp = false
    private var canComplete = false
    private var goalContext: JSONObject? = null
    private var completionNotice = false
    private var status: TextView? = null
    private var captureButton: View? = null
    private var complete: View? = null
    private var stop: View? = null
    private var contextCard: LinearLayout? = null
    private var goalLabel: TextView? = null
    private var goalStatus: TextView? = null
    private var displayed: LocalGuide? = null
    private var renderBasis: Basis? = null
    private var renderOfferId: String? = null
    private val settingsNotice = SettingsGuidanceNotification(activity)
    private var lastSettingsNotice: Pair<Basis, String>? = null
    private var settingsNoticeActive = false

    private val host = AndroidSurfaceHost(activity) { action ->
        if (action == SurfaceUserEvent.DISMISS_GUIDE) {
            send("help.stop")
            stopCapture()
        } else {
            activity.startActivity(Intent(activity, MvpActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }
    }

    private val service = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName, binder: IBinder) {
            controller = binder as ProjectionCaptureService.Controller
        }

        override fun onServiceDisconnected(name: ComponentName) {
            controller = null
            stopCapture()
        }
    }

    private val bound = activity.bindService(Intent(activity, ProjectionCaptureService::class.java),
        service, Context.BIND_AUTO_CREATE)

    init {
        scope.launch {
            while (isActive) {
                readFrame()
                render()
                delay(160)
            }
        }
    }

    fun draw(root: LinearLayout, paired: Boolean, inCall: Boolean) {
        this.paired = paired
        status = null
        captureButton = null
        complete = null
        stop = null
        contextCard = null
        goalLabel = null
        goalStatus = null
        if (!paired) {
            resetChannel()
            return
        }
        connect()
        if (inCall) {
            status = root.body("", 16f, Palette.BLUE)
            captureButton = root.secondary("화면 안내 시작") { scope.launch { requestCapture() } }
        }
        contextCard = root.card(Palette.CREAM) {
            body("현재 목적", 15f, Palette.BLUE)
            goalLabel = body("", 19f, Palette.INK)
            body("현재 상태", 15f, Palette.BLUE)
            goalStatus = body("", 17f, Palette.INK)
        }
        stop = root.secondary("안내 끄기") {
            send("help.stop")
            stopCapture()
            updateButtons()
        }
        complete = root.secondary("도움 완료") { send("help.complete") }
        updateButtons()
    }

    private fun updateButtons() {
        status?.text = if (frames != null) "화면 안내가 켜졌어요" else if (connected) "화면 안내를 시작할 수 있어요" else "안내 서버 연결 중"
        status?.visibility = if (receivesHelp) View.VISIBLE else View.GONE
        captureButton?.visibility = if (receivesHelp && frames == null) View.VISIBLE else View.GONE
        complete?.visibility = if (canComplete) View.VISIBLE else View.GONE
        val context = goalContext
        val afterCall = context?.optString("mode") == "POST_CALL_AGENT"
        contextCard?.visibility = if (context != null) View.VISIBLE else View.GONE
        stop?.visibility = if (receivesHelp && (frames != null || afterCall)) View.VISIBLE else View.GONE
        goalLabel?.text = when (context?.optString("goal_id")) {
            "SET_FONT" -> "글자 크기 키우기"
            "SET_TIMEOUT" -> "화면 자동 꺼짐 시간 바꾸기"
            "CLOCK_ALARM" -> "알람 맞추기"
            "MAP_ROUTE" -> "길찾기"
            "SMS_SEND" -> "문자 보내기"
            "MAIL_OPEN" -> "받은 메일 열기"
            else -> "목표 확인 중"
        }
        goalStatus?.text = when {
            context?.optBoolean("arrival_suspected") == true -> "목표에 도착한 것 같아요. 확인해 주세요."
            context?.optBoolean("guide_shown") == true -> "도움받는 화면에 안내를 표시했어요."
            afterCall -> "통화가 끝났어요. 안내를 이어가는 중이에요."
            else -> "통화 중 · 진행 중"
        }
    }

    fun takeCompletionNotice(): Boolean = completionNotice.also { completionNotice = false }

    private fun connect() {
        val next = store.token ?: return
        if (socket != null && token == next) return
        resetChannel()
        token = next
        val endpoint = store.server.replaceFirst("http", "ws") + "/v1/events"
        socket = http.newWebSocket(Request.Builder().url(endpoint).header("Authorization", "Bearer $next").build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    scope.launch { if (socket == webSocket) { connected = true; updateButtons() } }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    scope.launch { if (socket == webSocket) runCatching { receive(JSONObject(text)) } }
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(code, null)
                    reconnect(webSocket)
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = reconnect(webSocket)
            })
    }

    private fun reconnect(webSocket: WebSocket) {
        scope.launch {
            if (socket != webSocket) return@launch
            resetChannel()
            updateButtons()
            delay(1_000)
            if (paired && socket == null) connect()
        }
    }

    private fun resetChannel() {
        socket?.cancel()
        socket = null
        connected = false
        token = null
        callId = null
        helpId = null
        traceId = null
        receivesHelp = false
        canComplete = false
        goalContext = null
        instruction = null
        t0 = null
        mode = HelpMode.CALL_CHILD_LED
        stopCapture()
    }

    private fun receive(event: JSONObject) {
        if (event.optString("pair_id") != store.pairId) return
        val type = event.getString("type")
        val incomingHelp = event.stringOrNull("help_id") ?: return
        val payload = event.optJSONObject("payload") ?: return
        if (type == "help.view" && incomingHelp != helpId) {
            stopCapture()
            helpId = incomingHelp
            callId = event.stringOrNull("call_id")
            lastScreenRevision = 0
            mode = HelpMode.CALL_CHILD_LED
            instruction = null
            t0 = null
            goalContext = null
            completionNotice = false
        }
        if (incomingHelp != helpId) return
        logEvent(type, payload)
        when (type) {
            "help.view" -> {
                val view = payload.optJSONObject("parent_view")
                receivesHelp = view != null && view.optString("help_state") != "ENDED"
                if (view != null) {
                    val shownMode = HelpMode.valueOf(view.getString("mode"))
                    if (mode != shownMode) applyMode(shownMode)
                    setInstruction(view.optJSONObject("instruction")?.longOrNull("instruction_revision"))
                    if (view.optString("help_state") == "ENDED") stopCapture()
                }
                canComplete = payload.optJSONObject("child_view")?.optBoolean("can_complete") == true
                goalContext = payload.optJSONObject("goal_context")
                updateButtons()
            }
            "instruction.accepted" -> setInstruction(payload.longOrNull("instruction_revision"))
            "assist.session" -> {
                traceId = payload.stringOrNull("trace_id")
                val revision = payload.optJSONObject("basis")?.longOrNull("screen_revision")
                if (revision != null) lastScreenRevision = maxOf(lastScreenRevision, revision)
            }
            "instruction.cleared" -> setInstruction(null)
            "mode.changed" -> applyMode(HelpMode.valueOf(payload.getString("mode")))
            "screen.reobserve" -> published = null
            "guide.suspend" -> { decision = null; clearOverlay("guide_suspend") }
            "assist.decision" -> {
                traceId = payload.stringOrNull("trace_id")
                decision = payload
                decisionAt = SystemClock.elapsedRealtime()
                if (payload.optJSONObject("verdict") != null) awaitingVerdict = false
            }
            "help.ended", "observation.stop" -> {
                if (type == "help.ended") {
                    completionNotice = payload.optString("reason").endsWith("_COMPLETED")
                    goalContext = null
                }
                receivesHelp = false
                canComplete = false
                stopCapture()
                updateButtons()
            }
        }
    }

    private fun setInstruction(next: Long?) {
        if (mode != HelpMode.CALL_CHILD_LED || instruction == next) return
        instruction = next
        t0 = next?.let { SystemClock.elapsedRealtime() }
        decision = null
        awaitingVerdict = false
        clearOverlay("new_instruction")
    }

    private fun applyMode(next: HelpMode) {
        mode = next
        t0 = null
        instruction = null
        decision = null
        awaitingVerdict = false
        local = null
        published = null
        clearOverlay("mode_change")
    }

    private suspend fun requestCapture() {
        if (frames != null) return
        val basis = captureBasis()
        if (basis == null) {
            status?.text = "두 사람이 안내에 동의하고 통화를 연결해 주세요."
            return
        }
        if (!settingsNotice.enabled()) {
            status?.text = "설정 화면에서 안내를 받으려면 효자손 알림을 허용해 주세요."
            activity.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName))
            return
        }
        approvalBasis = basis
        @Suppress("DEPRECATION")
        activity.startActivityForResult(ProjectionCaptureService.approvalIntent(activity, wholeDisplay = true), CAPTURE_REQUEST)
    }

    fun onApproval(result: Int, data: Intent?) {
        val requested = approvalBasis.also { approvalBasis = null } ?: return
        scope.launch {
            val basis = captureBasis() ?: return@launch
            if (basis != requested) return@launch
            startCapture(basis, result, data)
        }
    }

    private suspend fun captureBasis(): Basis? {
        if (!connected || !receivesHelp || helpId == null || controller == null || traceId == null) return null
        val basis = emptyBasis().copy(instruction_revision = null)
        val sessionToken = store.token ?: return null
        val response = withContext(Dispatchers.IO) {
            runCatching { serverRequest(store.server, "GET", "/v1/consent", sessionToken, null) }.getOrNull()
        } ?: return null
        if (!response.ok || basis != emptyBasis().copy(instruction_revision = null)) return null
        val consent = JSONObject(response.body)
        return basis.copy(consent_revision = consent.getLong("consent_revision"))
            .takeIf { consent.optBoolean("agent_allowed") }
    }

    private fun startCapture(basis: Basis, result: Int, data: Intent?) {
        val help = basis.help_id ?: return
        val opened = controller?.open(help, connected, true, ProjectionCaptureService.approvalFromResult(result, data),
            diagnostic("N-07", basis), onInvalidated = {
                val active = frames
                sendGap()
                local = null
                published = null
                decision = null
                if (active?.isClosed == true) frames = null
                flow = frames?.let(::observationFlow)
                clearOverlay("projection_invalidated")
                updateButtons()
            })
        val capture = opened?.data?.frame_source
        if (capture == null) {
            status?.text = "화면 공유 권한을 다시 확인해 주세요."
            return
        }
        frames = capture
        flow = observationFlow(capture)
        send("projection.started", JSONObject().put("projection_epoch", capture.epoch))
        updateButtons()
        // EXPERIMENT (#37): the parent just approved guidance, so leave the call screen for the home screen
        // where the help starts. This only moves our own UI to the background; it never touches another app.
        activity.startActivity(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun observationFlow(capture: ProjectionFrames) =
        ObservationFlow(capture.epoch, ScreenDetection(listOf(profile)), profile.id, profile.revision, deviceProfileVersion,
            scan = { input ->
                val ready = CountDownLatch(1)
                var features = FeatureScan(FeatureKind.TEXT, false, emptyList())
                val started = SystemClock.elapsedRealtime()
                scanner.value.scan(input, profile) { result -> features = result; ready.countDown() }
                val done = ready.await(1_200, TimeUnit.MILLISECONDS)
                // #32: OCR latency only. No recognized text, rects or pixels.
                Log.i(OCR_LOG, "scan_ms=${SystemClock.elapsedRealtime() - started} timed_out=${!done}")
                if (done) listOf(features) else emptyList()
            }, previousScreenRevision = lastScreenRevision, reuseIdenticalScans = true)

    private suspend fun readFrame() {
        val source = frames ?: return
        val pipeline = flow ?: return
        val captured = source.take() ?: return logFrames(null)
        val area = LocalRect(0, 0, captured.pixels.width, captured.pixels.height)
        val input = CaptureFrame(source.epoch, captured.pixels, captured.capturedMonoMs,
            area, area, activity.display?.rotation ?: 0, emptyList(), FrameSafety.ALLOWED)
        val diagnostic = diagnostic("B-03", emptyBasis().copy(projection_epoch = source.epoch))
        val observation = withContext(Dispatchers.Default) {
            captured.use { pipeline.process(input, diagnostic).stabilization }
        } ?: return logFrames("no_stabilization")
        logFrames("frame")
        if (frames != source || flow != pipeline) return
        // A settings notice names a button instead of pointing at a rect, so a layout-only change on the same
        // screen (OCR edge rows flickering) must not cancel it: the gate below re-checks that the target is
        // still visible and clears it otherwise. A screen change still clears at once.
        val layoutOnly = observation.locationInvalidated && observation.fromScreenRevision == null &&
            !observation.verdictPending && settingsNoticeActive && displayed == null
        if (observation.locationInvalidated && !layoutOnly) clearOverlay("location_invalidated")
        else if (observation.verdictPending) clearOverlay("verdict_pending")
        local = observation.local
        val screen = observation.screenToPublish ?: observation.local?.screen ?: return
        if (screen != published) {
            val previous = published
            val codes = screen.evidence_codes.toMutableList()
            codes += belowFoldEvidence(screen.screen_kind, screen.target_ids)
            if (observation.fromScreenRevision != null) codes += "stable_screen_transition"
            val body = JSONObject().put("projection_epoch", source.epoch)
                .put("screen_revision", screen.screen_revision).put("screen_kind", screen.screen_kind)
                .put("observation_health", if (screen.observation_health == ObservationHealth.STABLE) "ACTIVE" else screen.observation_health.name)
                .put("target_ids", JSONArray(screen.target_ids.filterNot { it.startsWith(PROBE) }))
                .put("evidence_codes", JSONArray(codes))
            observation.fromScreenRevision?.let { body.put("from_screen_revision", it).put("reversible", true) }
            // EXPERIMENT: privacy-free publish trace (kind, health, revision, target count only).
            Log.i(FRAME_LOG, "publish kind=${screen.screen_kind} health=${screen.observation_health} " +
                "rev=${screen.screen_revision} targets=${screen.target_ids.count { !it.startsWith(PROBE) }} " +
                "probes=${screen.target_ids.count { it.startsWith(PROBE) }} below_fold=${codes.any { it.startsWith("below_fold") }}")
            if (send("screen.observed", body)) {
                published = screen
                lastScreenRevision = screen.screen_revision
            }
            if (mode == HelpMode.POST_CALL_AGENT && screen.observation_health == ObservationHealth.STABLE && previous?.screen_revision != screen.screen_revision) {
                t0 = SystemClock.elapsedRealtime()
            }
            // A scroll inside one list passes through GAP frames and comes back as a new revision of the
            // same screen. That is not a navigation, the server sends no verdict for it, and waiting for
            // one hid the next guide until a new instruction. Only a change of screen kind awaits a verdict.
            val stable = screen.observation_health == ObservationHealth.STABLE
            val navigated = stable && lastStableKind != null && lastStableKind != screen.screen_kind
            if (mode == HelpMode.CALL_CHILD_LED && instruction != null && previous != null &&
                previous.screen_revision != screen.screen_revision && (navigated || !stable)) awaitingVerdict = true
            if (stable && lastStableKind == screen.screen_kind && previous?.observation_health != ObservationHealth.STABLE) {
                awaitingVerdict = false
            }
            if (stable) lastStableKind = screen.screen_kind
        }
    }

    private var homeHintShown = false

    /**
     * EXPERIMENT (#37): with a goal but the parent in an app the profile does not know (or our own call
     * screen), the first step is to go back to the home screen. Show it as a swipe hint above the
     * gesture bar. It stays until a known screen is observed, so GAP frames do not make it flicker.
     */
    private fun renderHomeHint(): Boolean {
        val goal = goalContext?.optString("goal_id").orEmpty()
        val away = frames != null && receivesHelp && connected && goal.isNotEmpty() &&
            published?.screen_kind == "unknown"
        if (!away) {
            if (homeHintShown) { host.clear(); homeHintShown = false }
            return false
        }
        if (!homeHintShown) {
            clearSettingsNotice("go_home")
            displayed = null
            host.clear()
            homeHintShown = host.showBanner(HOME_GO) && host.showSwipeHint(HOME_GO)
            if (homeHintShown) Log.i(NOTICE_LOG, "home_go_hint")
        }
        return true
    }

    private fun render() {
        if (renderHomeHint()) return
        val value = decision ?: return clearOverlay()
        val offerId = value.stringOrNull("decision_id") ?: return clearOverlay()
        val basis = value.optJSONObject("basis")?.let(::basisFrom) ?: return clearOverlay()
        val observation = local ?: return clearOverlay()
        if (!connected || !receivesHelp || basis.help_id != helpId || basis.call_id != callId ||
            (mode == HelpMode.CALL_CHILD_LED && (instruction == null || basis.instruction_revision != instruction))) {
            return clearOverlay()
        }
        val target = value.optJSONObject("target")?.let { target ->
            target.stringOrNull("target_id")?.let { id -> Scoped(CurrentTarget(id,
                TargetLocation.valueOf(target.getString("location_kind").uppercase()), target.codes()), basis) }
        }
        val verdict = value.optJSONObject("verdict")?.let {
            Scoped(ObservedVerdict(Verdict.valueOf(it.getString("verdict")), observation.captured_mono_ms), basis)
        }
        val recovery = value.optJSONObject("recovery")?.let { Scoped(VerifiedRecovery(it.getString("first_edge_ref")), basis) }
        val proposal = value.optJSONObject("guide")?.let { guide ->
            Scoped(PostCallProposal(GuideAction.valueOf(guide.getString("action")), guide.stringOrNull("target_id"),
                guide.getString("message_key"), decisionAt, guide.getLong("valid_for_ms")), basis)
        }
        val input = GateInput(mode, basis, basis.copy(projection_epoch = observation.screen.projection_epoch,
            screen_revision = observation.screen.screen_revision), observation.layout_revision,
            SystemClock.elapsedRealtime(), t0, withAppIcons(observation),
            if (awaitingVerdict) InputEvidence.PENDING else InputEvidence.CONTINUOUS_NO_CHANGE,
            target, verdict, recovery, proposal, policyDenied = value.stringOrNull("warning") != null)
        val diagnostic = diagnostic("B-06", basis)
        val gate = GuideGate.evaluate(input, diagnostic)
        val gateError = gate.diagnostics.terminal_error?.code
        logGate(gateError ?: gate.outcome, input, observation)
        val next = gate.data?.display
        if (next == null) {
            if (settingsNoticeActive && keepSettingsNoticeOn(gateError, observation.screen.observation_health)) return
            return clearOverlay("gate_${gateError ?: gate.outcome}")
        }
        // The supported Android Settings screen hides third-party overlay windows.
        if (observation.screen.screen_kind.startsWith("settings_")) {
            val targetId = if (mode == HelpMode.CALL_CHILD_LED) target?.value?.targetId else proposal?.value?.targetId
            val message = settingsGuidanceText(next, targetId) ?: return clearOverlay("no_allowed_text")
            if (displayed != null) clearOverlay("settings_screen")
            val key = basis.copy(event_seq = null, utterance_seq = null) to message
            if (lastSettingsNotice != key && settingsNotice.show(message)) {
                lastSettingsNotice = key
                settingsNoticeActive = true
                Log.i(NOTICE_LOG, "posted")
            }
            return
        }
        clearSettingsNotice("non_settings_screen")
        if (!Settings.canDrawOverlays(activity)) return clearOverlay("no_overlay_permission")
        // On the launcher a scroll guide means "open / scroll the app list": say how, instead of "화면 내리기".
        if (observation.screen.screen_kind == "home" && next.action == GuideAction.SCROLL_DOWN) {
            if (displayed == next && renderBasis == basis && renderOfferId == offerId) return
            if (displayed != null) host.clear()
            if (host.showBanner(HOME_SWIPE_UP) && host.showSwipeHint(HOME_SWIPE_UP)) {
                displayed = next
                renderBasis = basis
                renderOfferId = offerId
                Log.i(NOTICE_LOG, "home_swipe_banner")
                send("guide.shown", JSONObject().put("mode", mode.name).put("decision_id", offerId))
            }
            return
        }
        if (displayed == next && renderBasis == basis && renderOfferId == offerId) return
        val shown = ParentGuideFlow.run(input, diagnostic, host, true).data
        if (shown?.rendered == true) {
            displayed = next
            renderBasis = basis
            renderOfferId = offerId
            send("guide.shown", JSONObject().put("mode", mode.name).put("decision_id", offerId))
        }
    }

    private var frameWindowStart = 0L
    private val frameCounts = mutableMapOf<String, Int>()

    /**
     * The target is below the fold when it is not visible, a row above it in the fixed list is, and no
     * row below it is. Only then does the server offer SCROLL_DOWN (`below_fold_evidence`).
     */
    private fun belowFoldEvidence(kind: String, ids: Set<String>): List<String> = when (kind) {
        "settings_home" -> if ("settings.display" !in ids &&
            ids.any { it.startsWith(PROBE_ABOVE) } && ids.none { it.startsWith(PROBE_BELOW) }
        ) listOf("below_fold:settings_home:settings.display", "scroll_down:settings_home") else emptyList()
        // Launcher apps not on the current home page or app-list page are further up in the app list.
        "home" -> LAUNCHER_APPS.filterNot { it in ids }.map { "below_fold:home:$it" }
            .let { if (it.isEmpty()) it else it + "scroll_down:home" }
        else -> emptyList()
    }

    /**
     * On the launcher OCR finds only an app's label, which sits under its icon. Grow a launcher target's
     * rect up over the icon and out to the icon width so the red border frames the whole app.
     */
    private fun withAppIcons(observation: LocalObservation): LocalObservation {
        if (observation.screen.screen_kind != "home") return observation
        val density = activity.resources.displayMetrics.density
        val screenWidth = activity.resources.displayMetrics.widthPixels
        fun px(dp: Int) = (dp * density + 0.5f).toInt()
        return observation.copy(targets = observation.targets.map { target ->
            if (!target.target_id.startsWith("launcher.")) return@map target
            val r = target.rect
            val half = maxOf((r.right - r.left) / 2 + px(8), px(40))
            val center = (r.left + r.right) / 2
            target.copy(rect = LocalRect((center - half).coerceAtLeast(0), (r.top - px(88)).coerceAtLeast(0),
                (center + half).coerceAtMost(screenWidth), r.bottom + px(6)))
        })
    }

    /** EXPERIMENT: per-2s counts of loop outcomes (no frame / no stabilization / processed). */
    private fun logFrames(kind: String?) {
        val key = kind ?: "no_frame"
        frameCounts[key] = (frameCounts[key] ?: 0) + 1
        val now = SystemClock.elapsedRealtime()
        if (now - frameWindowStart >= 2_000) {
            if (frameWindowStart != 0L) Log.i(FRAME_LOG, "window ${frameCounts.toSortedMap()}")
            frameWindowStart = now
            frameCounts.clear()
        }
    }

    /** Instruction chain trace: event type and revisions only. No utterance, target text or rects. */
    private fun logEvent(type: String, payload: JSONObject) {
        if (type !in setOf("instruction.accepted", "instruction.cleared", "assist.decision", "guide.suspend",
                "mode.changed", "screen.reobserve", "help.ended", "observation.stop")) return
        val basis = payload.optJSONObject("basis")
        val revision = payload.longOrNull("instruction_revision") ?: basis?.longOrNull("instruction_revision")
        val extra = if (type == "assist.decision") " target=${payload.optJSONObject("target") != null} " +
            "verdict=${payload.optJSONObject("verdict") != null} screen_rev=${basis?.longOrNull("screen_revision")}" else ""
        Log.i(EVENT_LOG, "recv=$type instr_rev=$revision current_instr=$instruction mode=$mode$extra")
    }

    private var lastGateLog = ""

    /** #32: reason, frame age and revisions only. No utterance, text, rect or pixels. */
    private fun logGate(reason: String?, input: GateInput, observation: LocalObservation) {
        val line = "gate=$reason age_ms=${input.now_mono_ms - observation.captured_mono_ms} " +
            "t0_ms=${input.t0_mono_ms?.let { input.now_mono_ms - it }} screen_rev=${observation.screen.screen_revision} " +
            "layout_rev=${observation.layout_revision} instr_rev=${input.basis.instruction_revision} " +
            "pending=${input.inputEvidence == InputEvidence.PENDING}"
        val key = "$reason ${input.inputEvidence} ${observation.screen.screen_revision} ${observation.layout_revision}"
        if (key == lastGateLog) return
        lastGateLog = key
        Log.i("HyojasonGate", line)
    }

    private fun emptyBasis() = Basis(store.pairId, callId, helpId, null, null, null, null, instruction, frames?.epoch, null, null)

    private fun diagnostic(node: String, basis: Basis) = Diagnostic(traceId ?: "local-capture", UUID.randomUUID().toString(),
        node, Source.LIVE, basis, emptyList(), 0, emptyMap(), null)

    private fun basisFrom(json: JSONObject) = Basis(json.stringOrNull("pair_id"), json.stringOrNull("call_id"),
        json.stringOrNull("help_id"), json.longOrNull("event_seq"), json.longOrNull("consent_revision"),
        json.longOrNull("goal_revision"), json.longOrNull("mode_revision"), json.longOrNull("instruction_revision"),
        json.stringOrNull("projection_epoch"), json.longOrNull("screen_revision"), json.longOrNull("utterance_seq"))

    private fun JSONObject.stringOrNull(key: String) = if (isNull(key)) null else getString(key)
    private fun JSONObject.longOrNull(key: String) = if (isNull(key)) null else getLong(key)
    private fun JSONObject.codes(): Set<String> = optJSONArray("evidence_codes")?.let { array ->
        (0 until array.length()).mapTo(mutableSetOf()) { array.getString(it) }
    }.orEmpty()

    private fun send(type: String, payload: JSONObject = JSONObject()): Boolean {
        if (!connected) return false
        val pair = store.pairId ?: return false
        val call = callId ?: return false
        val help = helpId ?: return false
        val body = JSONObject().put("v", 1).put("type", type).put("event_id", UUID.randomUUID().toString())
            .put("pair_id", pair).put("call_id", call).put("help_id", help).put("payload", payload)
        return socket?.send(body.toString()) == true
    }

    private fun sendGap() {
        val screen = published ?: return
        send("screen.observed", JSONObject().put("projection_epoch", screen.projection_epoch)
            .put("screen_revision", screen.screen_revision).put("screen_kind", "unknown").put("observation_health", "GAP")
            .put("target_ids", JSONArray()).put("evidence_codes", JSONArray()))
    }

    private fun clearOverlay(reason: String = "render") {
        clearSettingsNotice(reason)
        if (displayed != null) host.clear()
        displayed = null
        renderOfferId = null
        renderBasis = null
    }

    /** Reason codes only: no utterance, screen text, rect or pixels. */
    private fun clearSettingsNotice(reason: String) {
        if (settingsNoticeActive) {
            settingsNotice.clear()
            Log.i(NOTICE_LOG, "cancel reason=$reason")
        }
        settingsNoticeActive = false
    }

    private fun stopCapture() {
        approvalBasis = null
        controller?.revokeConsent()
        frames = null
        flow = null
        local = null
        published = null
        decision = null
        lastStableKind = null
        if (homeHintShown) { host.clear(); homeHintShown = false }
        clearOverlay("capture_stopped")
        lastSettingsNotice = null
    }

    override fun close() {
        paired = false
        resetChannel()
        scope.cancel()
        if (scanner.isInitialized()) scanner.value.close()
        if (bound) activity.unbindService(service)
    }

    companion object {
        const val CAPTURE_REQUEST = 42
        private const val NOTICE_LOG = "HyojasonNotice"
        private const val EVENT_LOG = "HyojasonEvent"
        private const val OCR_LOG = "HyojasonOcr"
        private const val FRAME_LOG = "HyojasonFrame"
        private const val PROBE = "probe."
        private const val HOME_SWIPE_UP = "위로 쓸어 올려 앱 목록을 열어 주세요"
        private const val HOME_GO = "화면 맨 아래를 위로 밀어 홈으로 가 주세요"
        private val LAUNCHER_APPS = listOf(
            "launcher.settings", "launcher.clock", "launcher.maps", "launcher.sms", "launcher.gmail")
        private const val PROBE_ABOVE = "probe.above#"
        private const val PROBE_BELOW = "probe.below#"
        private val SETTINGS_ROWS_ABOVE_DISPLAY = listOf(
            "네트워크 및 인터넷", "연결된 기기", "앱", "알림", "배터리", "저장용량", "소리 및 진동")
        private val SETTINGS_ROWS_BELOW_DISPLAY = listOf(
            "배경화면 및 스타일", "접근성", "보안 및 개인 정보 보호", "위치", "안전 및 긴급 상황",
            "디지털 웰빙 및 자녀 보호 기능", "시스템", "도움말 및 지원")
    }
}
