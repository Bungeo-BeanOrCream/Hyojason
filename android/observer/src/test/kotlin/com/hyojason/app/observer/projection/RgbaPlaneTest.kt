package com.hyojason.app.observer.projection

import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RgbaPlaneTest {
    @Test fun b03_1_packedRowsSkipPaddingAndPreserveArgbChannels() {
        val input = ByteBuffer.allocateDirect(20).order(ByteOrder.BIG_ENDIAN)
        input.putInt(0, 0xff000080.toInt()).putInt(4, 0x00ff00ff).putInt(8, 0xaaaaaaaa.toInt())
        input.putInt(12, 0x0000ff40).putInt(16, 0x12345678)

        assertArrayEquals(
            intArrayOf(0x80ff0000.toInt(), 0xff00ff00.toInt(), 0x400000ff, 0x78123456),
            copyRgbaPlane(input.order(ByteOrder.LITTLE_ENDIAN), 2, 2, rowStride = 12, pixelStride = 4),
        )
    }

    @Test fun b03_1_stridedPixelsSkipPaddingAndRejectShortBuffer() {
        val input = ByteBuffer.allocate(13).order(ByteOrder.BIG_ENDIAN)
        input.putInt(0, 0x12345678).putInt(6, 0x11223344)

        assertArrayEquals(
            intArrayOf(0x78123456, 0x44112233),
            copyRgbaPlane(input, 2, 1, rowStride = 13, pixelStride = 6),
        )
        input.limit(9)
        assertNull(copyRgbaPlane(input, 2, 1, rowStride = 13, pixelStride = 6))
    }
}
