package com.hyojason.app.observer.projection

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Copies only the visible pixels, even if the ImageReader pads rows or pixels. */
internal fun copyRgbaPlane(
    buffer: ByteBuffer,
    width: Int,
    height: Int,
    rowStride: Int,
    pixelStride: Int,
): IntArray? {
    if (width <= 0 || height <= 0 || rowStride <= 0 || pixelStride < Int.SIZE_BYTES) return null
    val bytesPerRow = (width - 1L) * pixelStride + Int.SIZE_BYTES
    val pixelCount = width.toLong() * height
    val requiredBytes = (height - 1L) * rowStride + bytesPerRow
    if (bytesPerRow > rowStride || requiredBytes > buffer.limit() || pixelCount > Int.MAX_VALUE) return null

    val rgba = buffer.duplicate().order(ByteOrder.BIG_ENDIAN)
    val argb = IntArray(pixelCount.toInt())
    if (pixelStride == Int.SIZE_BYTES && rowStride % Int.SIZE_BYTES == 0) {
        rgba.position(0)
        val packed = rgba.asIntBuffer()
        val rowStep = rowStride / Int.SIZE_BYTES
        for (row in 0 until height) {
            packed.position(row * rowStep)
            packed.get(argb, row * width, width)
        }
    } else {
        for (row in 0 until height) {
            val start = row * rowStride
            for (column in 0 until width) {
                argb[row * width + column] = rgba.getInt(start + column * pixelStride)
            }
        }
    }
    for (index in argb.indices) {
        argb[index] = Integer.rotateRight(argb[index], Byte.SIZE_BITS)
    }
    return argb
}
