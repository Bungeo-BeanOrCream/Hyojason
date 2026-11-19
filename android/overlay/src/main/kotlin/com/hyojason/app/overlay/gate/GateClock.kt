package com.hyojason.app.overlay.gate

import com.hyojason.app.contract.HelpMode

/** Owned by the serialized parent event loop. Returns true when it invalidates the previous guide. */
class GateClock {
    private var helpId: String? = null
    private var mode: HelpMode? = null
    private var modeRevision: Long? = null
    private var instructionRevision: Long? = null
    private var stableScreen: Pair<String, Long>? = null
    private val seenInstructions = mutableSetOf<String>()

    var t0_mono_ms: Long? = null
        private set

    fun applyMode(helpId: String, mode: HelpMode, revision: Long): Boolean {
        if (this.helpId == helpId && revision <= (modeRevision ?: -1)) return false
        if (this.helpId != helpId) {
            seenInstructions.clear()
            instructionRevision = null
        }
        this.helpId = helpId
        this.mode = mode
        modeRevision = revision
        stableScreen = null
        t0_mono_ms = null
        return true
    }

    fun acceptInstruction(id: String, revision: Long, nowMonoMs: Long): Boolean {
        if (mode != HelpMode.CALL_CHILD_LED || revision <= (instructionRevision ?: -1)) return false
        if (!seenInstructions.add(id)) return false
        instructionRevision = revision
        t0_mono_ms = nowMonoMs
        return true
    }

    fun stableScreen(epoch: String, revision: Long, nowMonoMs: Long): Boolean {
        val screen = epoch to revision
        if (mode != HelpMode.POST_CALL_AGENT || stableScreen == screen) return false
        stableScreen = screen
        t0_mono_ms = nowMonoMs
        return true
    }

    internal fun cancelTimer() {
        stableScreen = null
        t0_mono_ms = null
    }

    fun clear() {
        helpId = null
        mode = null
        modeRevision = null
        instructionRevision = null
        seenInstructions.clear()
        cancelTimer()
    }
}
