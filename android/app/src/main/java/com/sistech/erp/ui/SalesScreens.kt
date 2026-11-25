package com.sistech.erp.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.sistech.erp.data.Api
import com.sistech.erp.data.objects
import com.sistech.erp.data.str
import kotlinx.coroutines.delay

@Composable
fun SalesHomeScreen(api: Api, back: () -> Unit, openWeb: (String) -> Unit) {
    val s = rememberLoader { api.get("sales/summary/") }
    Page("My sales", back, eyebrow = "Sales") {
        LoadState(s) { d ->
            val m = d.getJSONObject("month")
            val pct = m.str("target_pct")
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(vertical = 12.dp)) {
                item {
                    KpiRow({ Kpi("Sales this month", rupeesShort(m.str("value")), "before GST", modifier = it) },
                        { Kpi("Target", if (m.str("target") == "0") "—" else rupeesShort(m.str("target")), if (pct.isNotBlank() && pct != "null") "$pct% achieved" else "no target set",
                            tone = if ((pct.toBigDecimalOrNull() ?: java.math.BigDecimal.ZERO) >= java.math.BigDecimal(100)) KpiTone.Good else KpiTone.Normal, modifier = it) })
                }
                item {
                    val leads = d.getJSONObject("leads")
                    KpiRow({ Kpi("Open orders", d.optInt("open_orders").toString(), "${d.optInt("quotes")} quotations", modifier = it) },
                        { Kpi("Leads", leads.optInt("open").toString(), "${leads.optInt("follow_ups")} follow-ups due", modifier = it) })
                }
                item { SecondaryButton("My charts") { openWeb("/sales/team/") } }
                item { SectionTitle("Waiting for you") }
                val work = d.getJSONArray("work").objects()
                items(work) { w -> ListRow(w.str("label"), w.str("hint"), w.optInt("count").toString()) }
                if (work.isEmpty()) item { Empty("All clear — nothing is waiting for you.") }
            }
        }
    }
}

@Composable
fun CustomersScreen(api: Api, back: () -> Unit, openWeb: (String) -> Unit) {
    var q by remember { mutableStateOf("") }
    var debounced by remember { mutableStateOf("") }
    LaunchedEffect(q) { delay(350); debounced = q }
    val list = rememberLoader(debounced) { api.get("sales/customers/", mapOf("q" to debounced)).getJSONArray("customers").objects() }
    Page("Customers", back, eyebrow = "Sales") {
        OutlinedTextField(q, { q = it }, label = { Text("Search name, code, city or phone") }, singleLine = true, modifier = Modifier.fillMaxWidth().padding(top = 8.dp))
        LoadState(list) { rows ->
            LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
                items(rows) { c ->
                    val cr = c.getJSONObject("credit")
                    val overdue = (cr.str("overdue").toBigDecimalOrNull()?.signum() ?: 0) > 0
                    ListRow(c.str("name"), "${c.str("code")} · ${c.str("kind")}${if (c.str("city").isNotBlank()) " · " + c.str("city") else ""}",
                        rupees(cr.str("available")), "available", chip = if (overdue) "Overdue ${rupees(cr.str("overdue"))}" else "", chipTone = Danger) {
                        openWeb("/partners/${c.getInt("id")}/")
                    }
                }
                if (rows.isEmpty()) item { Empty("No customers match.") }
            }
        }
    }
}

@Composable
fun DealerRequestsScreen(api: Api, back: () -> Unit, openWeb: (String) -> Unit) {
    val list = rememberLoader { api.get("sales/dealer-requests/").getJSONArray("requests").objects() }
    Page("Dealer requests", back, eyebrow = "Sales") {
        LoadState(list) { rows ->
            LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
                items(rows) { r ->
                    val lines = r.getJSONArray("lines").objects().joinToString(", ") { "${it.str("product")} × ${qty(it.str("quantity"))}" }
                    ListRow("${r.str("number")} · ${r.str("dealer")}", "needed by ${r.str("required_by")} · $lines", rupees(r.str("estimate"))) {
                        openWeb("/sales/dealer-requests/${r.getInt("id")}/")
                    }
                }
                if (rows.isEmpty()) item { Empty("No dealer requests waiting.") }
            }
        }
    }
}

@Composable
fun OrdersScreen(api: Api, back: () -> Unit, openWeb: (String) -> Unit) {
    val list = rememberLoader { api.get("sales/orders/").getJSONArray("orders").objects() }
    Page("My orders", back, eyebrow = "Sales", actions = { TextButton(onClick = { openWeb("/orders/new/Sale/") }) { Text("New") } }) {
        LoadState(list) { rows ->
            LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
                items(rows) { o ->
                    ListRow(o.str("number"), "${o.str("customer")} · ${o.str("site")} · due ${o.str("due_date")}", rupees(o.str("total")),
                        chip = o.str("status"), chipTone = statusTone(o.str("status"))) { openWeb("/orders/${o.getInt("id")}/") }
                }
                if (rows.isEmpty()) item { Empty("No orders yet.") }
            }
        }
    }
}
