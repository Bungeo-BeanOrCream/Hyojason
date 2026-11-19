package com.hyojason.app.child

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.text.InputFilter
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

object Palette {
    val BG = Color.parseColor("#FFFDF9")
    val ORANGE = Color.parseColor("#D9580C")
    val PRIMARY = Color.parseColor("#BC4807")
    val CREAM = Color.parseColor("#FFF1E0")
    val BLUE = Color.parseColor("#1D5B79")
    val SKY = Color.parseColor("#E6F1F7")
    val MIST = Color.parseColor("#F2F8FC")
    val RED = Color.parseColor("#C92A38")
    val BLUSH = Color.parseColor("#FFEAEA")
    val INK = Color.parseColor("#2B231D")
    val MUTED = Color.parseColor("#5A5047")
    val LINE = Color.parseColor("#9BB9CA")
}

internal fun Context.dp(value: Int) = (value * resources.displayMetrics.density).toInt()

private fun panel(context: Context, fill: Int, stroke: Int? = null, radius: Int = 10) =
    GradientDrawable().apply {
        cornerRadius = context.dp(radius).toFloat()
        setColor(fill)
        stroke?.let { setStroke(context.dp(1), it) }
    }

private fun <T : View> LinearLayout.block(view: T, top: Int): T {
    addView(view, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        .apply { topMargin = context.dp(top) })
    return view
}

fun LinearLayout.brand() {
    val row = LinearLayout(context).apply {
        gravity = Gravity.CENTER_VERTICAL
        background = panel(context, Palette.CREAM, Palette.ORANGE, 8)
        setPadding(context.dp(6), 0, context.dp(13), 0)
    }
    row.addView(ImageView(context).apply {
        setImageResource(R.drawable.hyojason_team_logo)
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
    }, LinearLayout.LayoutParams(context.dp(37), context.dp(37)))
    row.addView(TextView(context).apply {
        text = "효자손"
        textSize = 16f
        typeface = Typeface.DEFAULT_BOLD
        setTextColor(Palette.INK)
    })
    addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT))
}

fun LinearLayout.title(value: String) = block(TextView(context).apply {
    text = value
    setTextColor(Palette.INK)
    textSize = 29f
    typeface = Typeface.DEFAULT_BOLD
    setAccessibilityHeading(true)
}, 20)

fun LinearLayout.body(value: String, size: Float = 17f, color: Int = Palette.MUTED) =
    block(TextView(context).apply {
        text = value
        setTextColor(color)
        textSize = size
        setLineSpacing(0f, 1.15f)
    }, 8)

fun LinearLayout.chip(value: String) = TextView(context).apply {
    text = value
    setTextColor(Palette.BLUE)
    textSize = 14f
    typeface = Typeface.DEFAULT_BOLD
    background = panel(context, Palette.SKY, Palette.LINE, 6)
    setPadding(context.dp(10), context.dp(5), context.dp(10), context.dp(5))
    this@chip.addView(this, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        .apply { topMargin = context.dp(12) })
}

private fun LinearLayout.action(value: String, fill: Int, ink: Int, stroke: Int?, onClick: () -> Unit) =
    block(Button(context).apply {
        text = value
        setTextColor(ink)
        textSize = 18f
        typeface = Typeface.DEFAULT_BOLD
        isAllCaps = false
        minHeight = context.dp(54)
        setPadding(context.dp(12), context.dp(10), context.dp(12), context.dp(10))
        background = RippleDrawable(ColorStateList.valueOf(0x22000000), panel(context, fill, stroke), null)
        stateListAnimator = null
        setOnClickListener { onClick() }
    }, 10)

fun LinearLayout.primary(value: String, color: Int = Palette.PRIMARY, onClick: () -> Unit) =
    action(value, color, Color.WHITE, null, onClick)

fun LinearLayout.secondary(value: String, onClick: () -> Unit) =
    action(value, Color.WHITE, Palette.BLUE, Palette.LINE, onClick)

fun LinearLayout.choice(value: String, selected: Boolean, onClick: () -> Unit) =
    action(value, if (selected) Palette.CREAM else Palette.MIST, Palette.INK,
        if (selected) Palette.ORANGE else Palette.LINE, onClick).apply { isSelected = selected }

fun LinearLayout.toggle(left: String, right: String, leftOn: Boolean, onPick: (Boolean) -> Unit) =
    controls { choice(left, leftOn) { onPick(true) }; choice(right, !leftOn) { onPick(false) } }

fun LinearLayout.controls(fill: LinearLayout.() -> Unit) {
    val horizontal = resources.configuration.fontScale < 1.3f && resources.configuration.screenWidthDp >= 360
    val row = LinearLayout(context).apply {
        orientation = if (horizontal) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
        fill()
        if (horizontal) {
            for (index in 0 until childCount) {
                val child = getChildAt(index)
                child.layoutParams = (child.layoutParams as LinearLayout.LayoutParams).apply {
                    width = 0
                    weight = 1f
                    if (index > 0) marginStart = context.dp(8)
                }
            }
        }
    }
    block(row, 2)
}

fun LinearLayout.card(tint: Int = Color.WHITE, fill: LinearLayout.() -> Unit) =
    block(LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        background = panel(context, tint, Palette.LINE)
        setPadding(context.dp(15), context.dp(7), context.dp(15), context.dp(14))
        fill()
    }, 14)

fun LinearLayout.codeInput() = block(EditText(context).apply {
    hint = "번호 6자리"
    gravity = Gravity.CENTER
    inputType = InputType.TYPE_CLASS_NUMBER
    typeface = Typeface.MONOSPACE
    textSize = 30f
    setSingleLine()
    filters = arrayOf(InputFilter.LengthFilter(6))
    background = panel(context, Color.WHITE, Palette.BLUE)
    minHeight = context.dp(64)
    setSelectAllOnFocus(true)
}, 16)

fun LinearLayout.digits(value: String) {
    controls {
        for (group in value.chunked(3)) {
            card(Palette.CREAM) {
                body(group, 36f, Palette.INK).apply {
                    typeface = Typeface.MONOSPACE
                    gravity = Gravity.CENTER
                    contentDescription = group.toCharArray().joinToString(" ")
                }
            }
        }
    }
}

fun LinearLayout.spacer() = addView(View(context), LinearLayout.LayoutParams(0, 0, 1f))
