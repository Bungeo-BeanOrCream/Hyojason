package com.hyojason.app.child

import org.junit.Assert.assertEquals
import org.junit.Test

class FlowTest {
    @Test
    fun no_call_is_home() {
        assertEquals(Screen.HOME, callScreen(Role.PARENT, null))
    }

    @Test
    fun caller_waits_and_callee_sees_incoming_either_direction() {
        val byChild = CurrentCall("c1", Role.CHILD, accepted = false)
        assertEquals(Screen.OUTGOING, callScreen(Role.CHILD, byChild))
        assertEquals(Screen.INCOMING, callScreen(Role.PARENT, byChild))
        val byParent = byChild.copy(callerRole = Role.PARENT)
        assertEquals(Screen.OUTGOING, callScreen(Role.PARENT, byParent))
        assertEquals(Screen.INCOMING, callScreen(Role.CHILD, byParent))
    }

    @Test
    fun accepted_call_is_in_call_for_both() {
        val call = CurrentCall("c1", Role.PARENT, accepted = true)
        assertEquals(Screen.IN_CALL, callScreen(Role.PARENT, call))
        assertEquals(Screen.IN_CALL, callScreen(Role.CHILD, call))
    }

    @Test
    fun formatting() {
        assertEquals("00:42", elapsed(42))
        assertEquals("01:05", elapsed(65))
        assertEquals("123 456", spacedCode("123456"))
        assertEquals(Role.CHILD, Role.PARENT.other)
    }
}
