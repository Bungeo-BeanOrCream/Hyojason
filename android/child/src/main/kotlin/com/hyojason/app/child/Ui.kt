package com.hyojason.app.child

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

/** docs/assets/app-design 색. ponytail: 테마·리소스 대신 코드 상수. 화면이 늘면 res/values로. */
object Palette {
    val BG = Color.parseColor("#FFF8EF")
    val GREEN = Color.parseColor("#1B6B43")
    val GREEN_SOFT = Color.parseColor("#E3F1E8")
    val RED = Color.parseColor("#B3261E")
    val INK = Color.parseColor("#1C1B1F")
    val MUTED = Color.parseColor("#5F5B55")
    val LINE = Color.parseColor("#E5DCCB")
}

private fun Context.dp(v: Int) = (v * resources.displayMetrics.density).toInt()

private fun rounded(ctx: Context, fill: Int, stroke: Int? = null, radius: Int = 16) = GradientDrawable().apply {
    cornerRadius = ctx.dp(radius).toFloat()
    setColor(fill)
    stroke?.let { setStroke(ctx.dp(2), it) }
}

private fun LinearLayout.gap(v: View, top: Int): View {
    addView(v, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        .apply { topMargin = context.dp(top) })
    return v
}

fun LinearLayout.brand() = gap(TextView(context).apply {
    text = "효자손"; setTextColor(Palette.GREEN); textSize = 16f; typeface = Typeface.DEFAULT_BOLD
}, 0)

fun LinearLayout.title(t: String) = gap(TextView(context).apply {
    text = t; setTextColor(Palette.INK); textSize = 28f; typeface = Typeface.DEFAULT_BOLD
}, 24) as TextView

fun LinearLayout.body(t: String, size: Float = 17f, color: Int = Palette.MUTED) = gap(TextView(context).apply {
    text = t; setTextColor(color); textSize = size; setLineSpacing(0f, 1.3f)
}, 12) as TextView

fun LinearLayout.chip(t: String) = gap(TextView(context).apply {
    text = t; setTextColor(Palette.GREEN); textSize = 14f; typeface = Typeface.DEFAULT_BOLD
    background = rounded(context, Palette.GREEN_SOFT, radius = 20)
    setPadding(context.dp(14), context.dp(6), context.dp(14), context.dp(6))
}, 16).also { (it.layoutParams as LinearLayout.LayoutParams).width = LinearLayout.LayoutParams.WRAP_CONTENT } as TextView

/** 큰 기본 버튼. 고령 사용자 기준으로 높이 64dp, 글자 20sp. */
fun LinearLayout.primary(t: String, color: Int = Palette.GREEN, onClick: () -> Unit) = gap(Button(context).apply {
    text = t; setTextColor(Color.WHITE); textSize = 20f; typeface = Typeface.DEFAULT_BOLD; isAllCaps = false
    background = rounded(context, color); minHeight = context.dp(64); stateListAnimator = null
    setOnClickListener { onClick() }
}, 12) as Button

fun LinearLayout.secondary(t: String, onClick: () -> Unit) = gap(Button(context).apply {
    text = t; setTextColor(Palette.INK); textSize = 20f; typeface = Typeface.DEFAULT_BOLD; isAllCaps = false
    background = rounded(context, Color.WHITE, Palette.INK); minHeight = context.dp(64); stateListAnimator = null
    setOnClickListener { onClick() }
}, 12) as Button

/** 둘 중 하나를 고르는 토글(역할 선택). 고른 쪽만 초록으로 채운다. */
fun LinearLayout.toggle(left: String, right: String, leftOn: Boolean, onPick: (Boolean) -> Unit) {
    val row = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
    listOf(left to true, right to false).forEach { (t, isLeft) ->
        val on = isLeft == leftOn
        row.addView(Button(context).apply {
            text = t; textSize = 18f; typeface = Typeface.DEFAULT_BOLD; isAllCaps = false; stateListAnimator = null
            setTextColor(if (on) Color.WHITE else Palette.INK)
            background = rounded(context, if (on) Palette.GREEN else Color.WHITE, if (on) null else Palette.LINE)
            minHeight = context.dp(56)
            contentDescription = if (on) "$t, 선택됨" else t
            setOnClickListener { onPick(isLeft) }
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
            if (isLeft) marginEnd = context.dp(6) else marginStart = context.dp(6)
        })
    }
    gap(row, 16)
}

/** 흰 카드. 안에 내용을 넣어 쓴다. */
fun LinearLayout.card(fill: LinearLayout.() -> Unit) = gap(LinearLayout(context).apply {
    orientation = LinearLayout.VERTICAL
    background = rounded(context, Color.WHITE, Palette.LINE)
    setPadding(context.dp(20), context.dp(16), context.dp(20), context.dp(20))
    fill()
}, 16)

fun LinearLayout.codeInput() = gap(EditText(context).apply {
    hint = "번호 6자리"; textSize = 28f; gravity = Gravity.CENTER; letterSpacing = 0.3f
    inputType = InputType.TYPE_CLASS_NUMBER; filters = arrayOf(android.text.InputFilter.LengthFilter(6))
    background = rounded(context, Color.WHITE, Palette.INK); minHeight = context.dp(64)
}, 16) as EditText

/** 남은 공간을 채워 버튼을 화면 아래로 민다. */
fun LinearLayout.spacer() = addView(View(context), LinearLayout.LayoutParams(0, 0, 1f))
