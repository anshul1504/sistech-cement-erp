package com.sistech.erp.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ListAlt
import androidx.compose.material.icons.automirrored.filled.ReceiptLong
import androidx.compose.material.icons.automirrored.filled.TrendingUp
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sistech.erp.data.Api
import com.sistech.erp.data.objects
import com.sistech.erp.data.str
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/** Role-wise home, laid out like the web launcher: hero, numbers, quick actions, waiting work, modules. */
@Composable
fun HomeScreen(api: Api, me: JSONObject, modules: List<JSONObject>, go: (String) -> Unit, openErp: (String) -> Unit) {
    val apps = me.optJSONArray("apps")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
    val user = me.getJSONObject("user")
    val isDealer = "dealer" in apps
    val isSales = "sales" in apps
    val work = rememberLoader { api.get("work/") }
    val dealer = if (isDealer) rememberLoader { api.get("dealer/summary/") } else null
    val sales = if (isSales) rememberLoader { api.get("sales/summary/") } else null
    val hour = LocalTime.now().hour
    val greeting = if (hour < 12) "Good morning" else if (hour < 17) "Good afternoon" else "Good evening"
    val waiting = work.data?.optInt("total") ?: 0

    LazyColumn(Modifier.fillMaxSize().background(Surface), contentPadding = PaddingValues(bottom = 24.dp)) {
        item {
            Hero(LocalDate.now().format(DateTimeFormatter.ofPattern("EEEE, d MMMM yyyy")), "$greeting,\n${user.str("name").substringBefore(' ')}",
                subtitle = if (isDealer && !me.isNull("dealer")) me.getJSONObject("dealer").str("name")
                    else me.optJSONArray("roles")?.let { r -> (0 until r.length()).joinToString(" · ") { r.getString(it) } }.orEmpty(),
                trailing = { Avatar(user.str("name")) { go("account") } }) {
                Row(Modifier.padding(top = 14.dp).clip(CircleShape).background(if (waiting > 0) Gold else Color(0x33FFFFFF)).clickable { go("work") }
                    .padding(horizontal = 14.dp, vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.NotificationsActive, null, tint = if (waiting > 0) Navy else Color.White, modifier = Modifier.size(16.dp))
                    Text(if (waiting > 0) "  $waiting waiting for you" else "  Nothing waiting for you",
                        style = MaterialTheme.typography.labelLarge.copy(color = if (waiting > 0) Navy else Color.White))
                }
            }
        }
        item {
            Column(Modifier.padding(horizontal = 16.dp)) {
                if (dealer != null) {
                    SectionHeader("My account", "Details") { go("dealer") }
                    LoadState(dealer) { d ->
                        val credit = d.getJSONObject("credit")
                        val overdue = (d.str("overdue").toBigDecimalOrNull()?.signum() ?: 0) > 0
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            KpiRow({ Kpi("Balance due", rupeesShort(d.str("balance")), "on your account", modifier = it) },
                                { Kpi("Credit available", rupeesShort(credit.str("available")), "of ${rupeesShort(credit.str("limit"))}", KpiTone.Good, it) })
                            KpiRow({ Kpi("Overdue", rupeesShort(d.str("overdue")), if (overdue) "clear to avoid holds" else "nothing overdue", if (overdue) KpiTone.Danger else KpiTone.Normal, it) },
                                { Kpi("Sold on this month", "${qty(d.getJSONObject("month").str("sold_mt"))} MT", "to retailers", modifier = it) })
                        }
                    }
                    SectionHeader("Quick actions")
                    Grid2(listOf(
                        { m -> QuickAction(Icons.Filled.AddShoppingCart, "Request order", "Products & quantity", moduleTone("sales"), m) { go("dealer/order") } },
                        { m -> QuickAction(Icons.AutoMirrored.Filled.ReceiptLong, "Ledger", "Statement & invoices", moduleTone("finance"), m) { go("dealer/ledger") } },
                        { m -> QuickAction(Icons.Filled.Storefront, "Sales to retailers", "Note what you sold", moduleTone("plant"), m) { go("dealer/secondary") } },
                        { m -> QuickAction(Icons.Filled.Download, "Statement PDF", "Download & share", moduleTone("people"), m) { openErp("/my-ledger/") } },
                    ))
                }
                if (sales != null) {
                    SectionHeader("This month", "My charts") { openErp("/sales/team/${user.optInt("id")}/") }
                    LoadState(sales) { d ->
                        val m = d.getJSONObject("month")
                        val pct = m.str("target_pct").toBigDecimalOrNull()
                        val leads = d.getJSONObject("leads")
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            KpiRow({ Kpi("My sales", rupeesShort(m.str("value")), "before GST", modifier = it) },
                                { Kpi("Target", if (pct != null) "$pct%" else "—", if (pct != null) "of ${rupeesShort(m.str("target"))}" else "no target set",
                                    if (pct != null && pct >= java.math.BigDecimal(100)) KpiTone.Good else KpiTone.Normal, it) })
                            KpiRow({ Kpi("Open orders", d.optInt("open_orders").toString(), "${d.optInt("quotes")} quotations", modifier = it) },
                                { Kpi("Follow-ups due", leads.optInt("follow_ups").toString(), "${leads.optInt("open")} open leads", if (leads.optInt("follow_ups") > 0) KpiTone.Warn else KpiTone.Normal, it) })
                        }
                    }
                    SectionHeader("Quick actions")
                    Grid2(listOf(
                        { mo -> QuickAction(Icons.Filled.AddCircle, "New order", "Sales order draft", moduleTone("sales"), mo) { openErp("/orders/new/Sale/") } },
                        { mo -> QuickAction(Icons.Filled.Inbox, "Dealer requests", "Turn into orders", moduleTone("people"), mo) { go("sales/requests") } },
                        { mo -> QuickAction(Icons.Filled.People, "Customers", "Credit & overdue", moduleTone("procurement"), mo) { go("sales/customers") } },
                        { mo -> QuickAction(Icons.AutoMirrored.Filled.ListAlt, "My orders", "Status & dispatch", moduleTone("finance"), mo) { go("sales/orders") } },
                    ))
                }
                val items = work.data?.getJSONArray("items")?.objects().orEmpty()
                if (items.isNotEmpty()) {
                    SectionHeader("Waiting for you", "See all") { go("work") }
                    Panel(padding = 10) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            items.take(3).forEach { w -> WorkRow(w.optInt("count"), w.str("label"), w.str("hint"), w.str("tone") == "danger") { openErp(w.str("path")) } }
                        }
                    }
                }
                val shown = modules.filter { it.str("key") != "dealer" }
                if (shown.isNotEmpty()) {
                    SectionHeader("Modules", if (shown.size > 3) "All ${shown.size}" else null) { go("erp") }
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        shown.take(3).forEach { m -> ModuleCardFor(m, openErp) }
                    }
                }
            }
        }
    }
}

@Composable
fun ModuleCardFor(m: JSONObject, openErp: (String) -> Unit) {
    val pages = m.optJSONArray("pages")?.objects().orEmpty().map { it.str("label") to it.str("path") }
    ModuleCard(m.str("key"), moduleIcon(m.str("key")), m.str("label"), m.str("blurb"), pages, { openErp(m.str("path")) }, openErp)
}

/** ERP tab: every module this login may open, as on the web launcher. */
@Composable
fun ModulesScreen(modules: List<JSONObject>, openErp: (String) -> Unit) {
    LazyColumn(Modifier.fillMaxSize().background(Surface), contentPadding = PaddingValues(bottom = 24.dp)) {
        item { Hero("Sistech ERP", "Modules", "Each module opens with its own dashboard and pages.") }
        items(modules) { m -> Box(Modifier.padding(start = 16.dp, end = 16.dp, top = 12.dp)) { ModuleCardFor(m, openErp) } }
        if (modules.isEmpty()) item { Box(Modifier.padding(16.dp)) { Empty("No modules are open for this login.") } }
    }
}

/** Work tab: the same list as the web bell / My work. */
@Composable
fun WorkScreen(api: Api, openErp: (String) -> Unit) {
    val work = rememberLoader { api.get("work/") }
    LazyColumn(Modifier.fillMaxSize().background(Surface), contentPadding = PaddingValues(bottom = 24.dp)) {
        item { Hero("My work", "Waiting for you", "Approvals you may give, deliveries without POD, follow-ups and more.") }
        item {
            Column(Modifier.padding(16.dp)) {
                LoadState(work) { d ->
                    val items = d.getJSONArray("items").objects()
                    if (items.isEmpty()) Panel { Text("All clear", style = MaterialTheme.typography.titleMedium.copy(color = Good)); Text("No approvals, deliveries or follow-ups are waiting for you.", style = MaterialTheme.typography.bodySmall) }
                    else Panel(padding = 10) {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            items.forEach { w -> WorkRow(w.optInt("count"), w.str("label"), w.str("hint"), w.str("tone") == "danger") { openErp(w.str("path")) } }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun Avatar(name: String, onClick: () -> Unit) {
    Box(Modifier.size(38.dp).clip(CircleShape).background(Gold).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Text(name.take(1).uppercase(), style = MaterialTheme.typography.titleMedium.copy(color = Navy, fontWeight = FontWeight.Bold))
    }
}

fun moduleIcon(key: String): ImageVector = when (key) {
    "sales" -> Icons.AutoMirrored.Filled.TrendingUp
    "people" -> Icons.Filled.Groups
    "procurement" -> Icons.Filled.ShoppingCart
    "plant" -> Icons.Filled.Factory
    "stock" -> Icons.Filled.Inventory2
    "logistics" -> Icons.Filled.LocalShipping
    "finance" -> Icons.Filled.AccountBalance
    "insights" -> Icons.Filled.Insights
    "admin" -> Icons.Filled.Settings
    "dealer" -> Icons.Filled.Storefront
    else -> Icons.Filled.Apps
}
