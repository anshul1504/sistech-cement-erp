package com.sistech.erp.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

// ---------- Data loading ----------

/** Load-once state for a screen: loading → data | error, with retry. */
class Loader<T>(private val scope: CoroutineScope, private val block: suspend () -> T) {
    var data by mutableStateOf<T?>(null); private set
    var error by mutableStateOf<String?>(null); private set
    var loading by mutableStateOf(false); private set
    fun load() {
        loading = true; error = null
        scope.launch {
            try { data = block() } catch (e: Exception) { error = e.message ?: "Something went wrong." } finally { loading = false }
        }
    }
}

@Composable
fun <T> rememberLoader(vararg keys: Any?, block: suspend () -> T): Loader<T> {
    val scope = rememberCoroutineScope()
    val loader = remember(*keys) { Loader(scope, block) }
    LaunchedEffect(loader) { loader.load() }
    return loader
}

@Composable
fun <T> LoadState(loader: Loader<T>, content: @Composable (T) -> Unit) {
    val d = loader.data
    when {
        d != null -> content(d)
        loader.error != null -> ErrorBox(loader.error!!) { loader.load() }
        else -> Box(Modifier.fillMaxWidth().padding(48.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Navy, strokeWidth = 3.dp) }
    }
}

// ---------- Shells ----------

/** Navy hero like the web sidebar: eyebrow, big title, subtitle and an optional trailing slot; gold rule at the bottom. */
@Composable
fun Hero(eyebrow: String, title: String, subtitle: String = "", onBack: (() -> Unit)? = null, trailing: @Composable RowScope.() -> Unit = {}, extra: @Composable ColumnScope.() -> Unit = {}) {
    StatusBarIcons(onDark = true)
    Column(Modifier.fillMaxWidth().background(Brush.verticalGradient(listOf(Navy, Navy2))).statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().padding(start = if (onBack != null) 4.dp else 20.dp, end = 12.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (onBack != null) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back", tint = Color.White) }
            Text(eyebrow.uppercase(), style = MaterialTheme.typography.labelSmall.copy(color = Color(0xFF9FB3D1)), modifier = Modifier.weight(1f))
            trailing()
        }
        Column(Modifier.padding(start = 20.dp, end = 20.dp, top = 2.dp, bottom = 20.dp)) {
            Text(title, style = MaterialTheme.typography.headlineMedium.copy(color = Color.White), maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (subtitle.isNotBlank()) Text(subtitle, style = MaterialTheme.typography.bodyMedium.copy(color = Color(0xFFC9D6EA)), modifier = Modifier.padding(top = 4.dp))
            extra()
        }
        Box(Modifier.fillMaxWidth().height(3.dp).background(Gold))
    }
}

/** Inner page: white bar with eyebrow + title (like the web page head), scrollable content on the grey surface. */
@Composable
fun Page(title: String, onBack: (() -> Unit)? = null, eyebrow: String = "", actions: @Composable RowScope.() -> Unit = {}, content: @Composable ColumnScope.() -> Unit) {
    StatusBarIcons(onDark = false)
    Column(Modifier.fillMaxSize().background(Surface)) {
        Column(Modifier.fillMaxWidth().background(Color.White).statusBarsPadding()) {
            Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 8.dp, top = 6.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                if (onBack != null) IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back", tint = Navy) } else Spacer(Modifier.width(16.dp))
                Column(Modifier.weight(1f)) {
                    if (eyebrow.isNotBlank()) Text(eyebrow.uppercase(), style = MaterialTheme.typography.labelSmall)
                    Text(title, style = MaterialTheme.typography.titleLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                actions()
            }
            HorizontalDivider(color = Line)
        }
        Column(Modifier.fillMaxSize().padding(horizontal = 16.dp), content = content)
    }
}

// ---------- Building blocks (mirror the web: org-card, inf-kpi, lx-card, lx-queue) ----------

@Composable
fun Panel(modifier: Modifier = Modifier, padding: Int = 16, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Color.White).border(1.dp, Line, RoundedCornerShape(16.dp)).padding(padding.dp), content = content)
}

@Composable
fun SectionCard(title: String? = null, modifier: Modifier = Modifier, trailing: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Panel(modifier) {
        if (title != null) Row(Modifier.fillMaxWidth().padding(bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(title, style = MaterialTheme.typography.titleMedium.copy(color = Ink), modifier = Modifier.weight(1f))
            if (trailing != null) Text(trailing, style = MaterialTheme.typography.labelMedium)
        }
        content()
    }
}

@Composable
fun SectionHeader(text: String, action: String? = null, onAction: (() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(top = 18.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.titleMedium.copy(color = Ink, fontWeight = FontWeight.Bold), modifier = Modifier.weight(1f))
        if (action != null && onAction != null) Text(action, style = MaterialTheme.typography.labelLarge.copy(color = Brand), modifier = Modifier.clickable(onClick = onAction).padding(4.dp))
    }
}

@Composable
fun SectionTitle(text: String) = SectionHeader(text)

enum class KpiTone { Normal, Warn, Danger, Good }

@Composable
fun Kpi(label: String, value: String, hint: String = "", tone: KpiTone = KpiTone.Normal, modifier: Modifier = Modifier, onClick: (() -> Unit)? = null) {
    val (bg, border, valueColor) = when (tone) {
        KpiTone.Warn -> Triple(Color(0xFFFFFAF0), Color(0xFFF4D27A), Color(0xFFA86B00))
        KpiTone.Danger -> Triple(Color(0xFFFFF5F4), Color(0xFFF1B8B2), Danger)
        KpiTone.Good -> Triple(Color.White, Line, Good)
        KpiTone.Normal -> Triple(Color.White, Line, Navy)
    }
    Column(modifier.clip(RoundedCornerShape(14.dp)).background(bg).border(1.dp, border, RoundedCornerShape(14.dp))
        .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier).padding(14.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium.copy(color = Ink2), maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(value, style = MaterialTheme.typography.titleLarge.copy(color = valueColor, fontSize = 22.sp), maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
        if (hint.isNotBlank()) Text(hint, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 2.dp))
    }
}

@Composable
fun KpiRow(vararg items: @Composable (Modifier) -> Unit) {
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(10.dp)) { items.forEach { it(Modifier.weight(1f).fillMaxHeight()) } }
}

@Composable
fun IconTile(icon: ImageVector, tone: Tone, size: Int = 44) {
    Box(Modifier.size(size.dp).clip(RoundedCornerShape((size / 3.6).dp)).background(tone.bg), contentAlignment = Alignment.Center) {
        Icon(icon, null, tint = tone.fg, modifier = Modifier.size((size * .5).dp))
    }
}

/** Square-ish quick action for a 2-column grid. */
@Composable
fun QuickAction(icon: ImageVector, title: String, subtitle: String, tone: Tone, modifier: Modifier = Modifier, badge: Int = 0, onClick: () -> Unit) {
    Column(modifier.clip(RoundedCornerShape(16.dp)).background(Color.White).border(1.dp, Line, RoundedCornerShape(16.dp)).clickable(onClick = onClick).padding(14.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            IconTile(icon, tone, 40)
            Spacer(Modifier.weight(1f))
            if (badge > 0) CountBadge(badge)
        }
        Text(title, style = MaterialTheme.typography.labelLarge.copy(color = Ink), maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 12.dp))
        Text(subtitle, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun Grid2(items: List<@Composable (Modifier) -> Unit>) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items.chunked(2).forEach { row ->
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { it(Modifier.weight(1f).fillMaxHeight()) }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
fun CountBadge(n: Int, color: Color = Danger) {
    Box(Modifier.defaultMinSize(minWidth = 24.dp, minHeight = 24.dp).clip(CircleShape).background(color).padding(horizontal = 7.dp), contentAlignment = Alignment.Center) {
        Text("$n", color = Color.White, style = MaterialTheme.typography.labelMedium.copy(color = Color.White, fontWeight = FontWeight.Bold))
    }
}

/** Web launcher card: coloured icon, title, blurb, page chips, "Open module →". */
@Composable
fun ModuleCard(key: String, icon: ImageVector, title: String, blurb: String, pages: List<Pair<String, String>>, onOpen: () -> Unit, onPage: (String) -> Unit) {
    val tone = moduleTone(key)
    Panel(Modifier.clickable(onClick = onOpen)) {
        Row(verticalAlignment = Alignment.Top) {
            IconTile(icon, tone, 48)
            Column(Modifier.weight(1f).padding(start = 14.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium.copy(color = Ink, fontWeight = FontWeight.Bold))
                Text(blurb, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        if (pages.isNotEmpty()) Row(Modifier.padding(top = 12.dp).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            pages.take(6).forEach { (label, path) ->
                Text(label, style = MaterialTheme.typography.labelMedium.copy(color = Ink2), modifier = Modifier.clip(CircleShape).background(Color(0xFFF3F6FB))
                    .clickable { onPage(path) }.padding(horizontal = 10.dp, vertical = 5.dp))
            }
        }
        Row(Modifier.padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Open module", style = MaterialTheme.typography.labelLarge.copy(color = Brand))
            Icon(Icons.AutoMirrored.Filled.ArrowForward, null, tint = Brand, modifier = Modifier.padding(start = 6.dp).size(16.dp))
        }
    }
}

/** "Waiting for you" row: gold count tile, label + hint, Open. */
@Composable
fun WorkRow(count: Int, label: String, hint: String, danger: Boolean = false, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color(0xFFF8FAFC)).clickable(onClick = onClick).padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(38.dp).clip(RoundedCornerShape(10.dp)).background(if (danger) Color(0xFFFDECEC) else GoldSoft), contentAlignment = Alignment.Center) {
            Text("$count", style = MaterialTheme.typography.titleMedium.copy(color = if (danger) Danger else Color(0xFF8A5A00), fontWeight = FontWeight.Bold))
        }
        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
            Text(label, style = MaterialTheme.typography.labelLarge.copy(color = Ink), maxLines = 2)
            if (hint.isNotBlank()) Text(hint, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Text("Open", style = MaterialTheme.typography.labelMedium.copy(color = Brand, fontWeight = FontWeight.SemiBold))
    }
}

@Composable
fun ActionTile(icon: ImageVector, title: String, subtitle: String, badge: Int = 0, tint: Color = Navy, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Row(modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Color.White).border(1.dp, Line, RoundedCornerShape(16.dp)).clickable(onClick = onClick).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically) {
        IconTile(icon, Tone(tint, tint.copy(alpha = .1f)))
        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium.copy(color = Ink), maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        if (badge > 0) CountBadge(badge)
        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = Ink3)
    }
}

/** List row inside a white panel (documents, customers, entries). */
@Composable
fun ListRow(title: String, subtitle: String = "", trailing: String = "", trailingSub: String = "", chip: String = "", chipTone: Color = Ink3, onClick: (() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp).clip(RoundedCornerShape(12.dp)).background(Color.White).border(1.dp, Line, RoundedCornerShape(12.dp))
        .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.labelLarge.copy(color = Ink), maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle.isNotBlank()) Text(subtitle, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (chip.isNotBlank()) Text(chip, style = MaterialTheme.typography.labelMedium.copy(color = chipTone, fontWeight = FontWeight.SemiBold),
                modifier = Modifier.padding(top = 6.dp).clip(CircleShape).background(chipTone.copy(alpha = .12f)).padding(horizontal = 8.dp, vertical = 2.dp))
        }
        if (trailing.isNotBlank()) Column(horizontalAlignment = Alignment.End, modifier = Modifier.padding(start = 10.dp)) {
            Text(trailing, style = MaterialTheme.typography.labelLarge.copy(color = Navy))
            if (trailingSub.isNotBlank()) Text(trailingSub, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
fun ErrorBox(message: String, onRetry: (() -> Unit)? = null) {
    Column(Modifier.fillMaxWidth().padding(vertical = 12.dp).clip(RoundedCornerShape(14.dp)).background(Color(0xFFFDECEC)).padding(16.dp)) {
        Text(message, style = MaterialTheme.typography.bodyMedium.copy(color = Danger))
        if (onRetry != null) Text("Try again", style = MaterialTheme.typography.labelLarge.copy(color = Brand), modifier = Modifier.padding(top = 8.dp).clickable(onClick = onRetry))
    }
}

@Composable
fun Empty(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 14.dp))
}

@Composable
fun PrimaryButton(text: String, enabled: Boolean = true, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Button(onClick = onClick, enabled = enabled, shape = RoundedCornerShape(12.dp), modifier = modifier.fillMaxWidth().height(52.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Navy, disabledContainerColor = Color(0xFFDCE1EA))) {
        Text(text, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
fun SecondaryButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    OutlinedButton(onClick = onClick, shape = RoundedCornerShape(12.dp), border = BorderStroke(1.dp, Line), modifier = modifier.fillMaxWidth().height(48.dp),
        colors = ButtonDefaults.outlinedButtonColors(containerColor = Color.White, contentColor = Navy)) {
        Text(text, style = MaterialTheme.typography.labelLarge)
    }
}

fun statusTone(s: String): Color = when (s) {
    "accepted", "Approved", "Completed", "active" -> Good
    "submitted", "Submitted", "Draft", "On hold" -> Warn
    "rejected", "cancelled", "Cancelled" -> Danger
    else -> Ink3
}

/** Status-bar icon colour for the screen behind it: light icons on the navy hero, dark icons on white bars. */
@Composable
fun StatusBarIcons(onDark: Boolean) {
    val view = androidx.compose.ui.platform.LocalView.current
    if (view.isInEditMode) return
    SideEffect {
        val window = (view.context as? android.app.Activity)?.window ?: return@SideEffect
        androidx.core.view.WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !onDark
    }
}
