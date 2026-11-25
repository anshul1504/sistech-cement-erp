package com.sistech.erp.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.sistech.erp.R
import java.math.BigDecimal
import java.text.NumberFormat
import java.util.Locale

val Navy = Color(0xFF082859)
val Navy2 = Color(0xFF061F45)
val Brand = Color(0xFF073777)
val Gold = Color(0xFFF4BD18)
val GoldSoft = Color(0xFFFFF4D6)
val Ink = Color(0xFF17233A)
val Ink2 = Color(0xFF34465F)
val Ink3 = Color(0xFF6B778A)
val Line = Color(0xFFE1E6EE)
val Surface = Color(0xFFF3F5F9)
val Danger = Color(0xFFB93A2F)
val Good = Color(0xFF1F7A5A)
val Warn = Color(0xFF8A5A00)

val Poppins = FontFamily(
    Font(R.font.poppins_regular, FontWeight.Normal),
    Font(R.font.poppins_medium, FontWeight.Medium),
    Font(R.font.poppins_semibold, FontWeight.SemiBold),
    Font(R.font.poppins_bold, FontWeight.Bold),
)

// No colour on the base style: Text inherits the container's content colour (white on buttons, ink on cards).
private val base = TextStyle(fontFamily = Poppins)

private val type = Typography(
    headlineMedium = base.copy(fontWeight = FontWeight.Bold, fontSize = 26.sp, color = Navy),
    titleLarge = base.copy(fontWeight = FontWeight.Bold, fontSize = 20.sp, color = Navy),
    titleMedium = base.copy(fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
    bodyLarge = base.copy(fontSize = 15.sp),
    bodyMedium = base.copy(fontSize = 14.sp),
    bodySmall = base.copy(fontSize = 12.5.sp, color = Ink3),
    labelLarge = base.copy(fontWeight = FontWeight.SemiBold, fontSize = 14.sp),
    labelMedium = base.copy(fontWeight = FontWeight.Medium, fontSize = 12.sp, color = Ink3),
    labelSmall = base.copy(fontWeight = FontWeight.SemiBold, fontSize = 11.sp, letterSpacing = 1.2.sp, color = Ink3),
)

/** Module colours — same as the web launcher tiles (static/erp/launcher.css). */
data class Tone(val fg: Color, val bg: Color)

fun moduleTone(key: String): Tone = when (key) {
    "sales" -> Tone(Color(0xFF1D5FBF), Color(0xFFE8F0FD))
    "people", "dealer" -> Tone(Color(0xFFA86B00), Color(0xFFFFF4D6))
    "procurement" -> Tone(Color(0xFF4B4FC2), Color(0xFFEEF0FF))
    "plant" -> Tone(Color(0xFF1F7A5A), Color(0xFFE9F6F1))
    "stock" -> Tone(Color(0xFF6B3FB4), Color(0xFFF3EEFC))
    "logistics" -> Tone(Color(0xFFB93A2F), Color(0xFFFDECEC))
    "finance" -> Tone(Color(0xFF19717F), Color(0xFFE7F5F7))
    "insights" -> Tone(Color.White, Navy)
    else -> Tone(Color(0xFF4A5668), Color(0xFFEEF1F5))
}

@Composable
fun SistechTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(primary = Navy, onPrimary = Color.White, secondary = Gold, onSecondary = Navy,
            background = Surface, surface = Color.White, onSurface = Ink, error = Danger, outline = Line),
        typography = type, content = content)
}

private val inr: NumberFormat = NumberFormat.getNumberInstance(Locale.forLanguageTag("en-IN")).apply { maximumFractionDigits = 0 }
private val inr2: NumberFormat = NumberFormat.getNumberInstance(Locale.forLanguageTag("en-IN")).apply { minimumFractionDigits = 2; maximumFractionDigits = 2 }

/** "123456.70" → "₹1,23,457" (Indian grouping). Blank → "—". */
fun rupees(v: String?, paise: Boolean = false): String {
    val d = v?.toBigDecimalOrNull() ?: return "—"
    return "₹" + (if (paise) inr2 else inr).format(d)
}

/** Compact: ₹3.25 L, ₹1.42 Cr. */
fun rupeesShort(v: String?): String {
    val d = v?.toBigDecimalOrNull() ?: return "—"
    val a = d.abs()
    val sign = if (d.signum() < 0) "−" else ""
    return when {
        a >= BigDecimal(10_000_000) -> sign + "₹" + a.divide(BigDecimal(10_000_000)).setScale(2, java.math.RoundingMode.HALF_UP).stripTrailingZeros().toPlainString() + " Cr"
        a >= BigDecimal(100_000) -> sign + "₹" + a.divide(BigDecimal(100_000)).setScale(2, java.math.RoundingMode.HALF_UP).stripTrailingZeros().toPlainString() + " L"
        else -> sign + "₹" + inr.format(a)
    }
}

fun qty(v: String?): String = v?.toBigDecimalOrNull()?.stripTrailingZeros()?.toPlainString() ?: "—"
