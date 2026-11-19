package com.hyojason.app.child

/** 가족 역할. 앱 토글로 각자 고른다(#6). 서버 값과 이름이 같다. */
enum class Role(val label: String) {
    PARENT("부모님"),
    CHILD("자녀");

    val other: Role get() = if (this == PARENT) CHILD else PARENT
}

/** `GET /v1/calls/current`에서 화면에 필요한 것만. */
data class CurrentCall(val callId: String, val callerRole: Role, val accepted: Boolean)

enum class Screen { CONNECT, CONSENT, HOME, OUTGOING, INCOMING, IN_CALL }

/** 페어가 확정된 뒤 서버의 진행 중 통화로 화면을 정한다(N-03). 발신은 누구나, 수락은 상대만. */
fun callScreen(me: Role, current: CurrentCall?): Screen = when {
    current == null -> Screen.HOME
    current.accepted -> Screen.IN_CALL
    current.callerRole == me -> Screen.OUTGOING
    else -> Screen.INCOMING
}

/** 통화 시간 `mm:ss`. 한 시간을 넘으면 분이 60 이상으로 간다(시연 통화는 짧다). */
fun elapsed(seconds: Long): String = "%02d:%02d".format(seconds / 60, seconds % 60)

/** 초대 코드 6자리를 읽기 쉽게 `123 456`. */
fun spacedCode(code: String): String = if (code.length == 6) "${code.take(3)} ${code.drop(3)}" else code
