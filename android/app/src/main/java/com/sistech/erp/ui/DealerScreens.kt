package com.sistech.erp.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ReceiptLong
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.ui.graphics.Color
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.sistech.erp.data.Api
import com.sistech.erp.data.objects
import com.sistech.erp.data.str
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate

@Composable
fun DealerHomeScreen(api: Api, back: () -> Unit, go: (String) -> Unit) {
    val s = rememberLoader { api.get("dealer/summary/") }
    Page("Account & credit", back, eyebrow = "My dealer account") {
        LoadState(s) { d ->
            val credit = d.getJSONObject("credit")
            val month = d.getJSONObject("month")
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(vertical = 12.dp)) {
                item { Text(d.getJSONObject("dealer").str("name"), style = MaterialTheme.typography.titleLarge) }
                item {
                    KpiRow({ Kpi("Balance", rupees(d.str("balance")), "positive = due", modifier = it) },
                        { Kpi("Overdue", rupees(d.str("overdue")), if ((d.str("overdue").toBigDecimalOrNull()?.signum() ?: 0) > 0) "clear to avoid holds" else "nothing overdue",
                            tone = if ((d.str("overdue").toBigDecimalOrNull()?.signum() ?: 0) > 0) KpiTone.Danger else KpiTone.Normal, modifier = it) })
                }
                item {
                    KpiRow({ Kpi("Credit limit", rupees(credit.str("limit")), "approved", modifier = it) },
                        { Kpi("Available", rupees(credit.str("available")), "after open orders", tone = KpiTone.Good, modifier = it) })
                }
                item {
                    KpiRow({ Kpi("Bought this month", "${qty(month.str("bought_mt"))} MT", "from Sistech", modifier = it) },
                        { Kpi("Sold to retailers", "${qty(month.str("sold_mt"))} MT", "as you noted", modifier = it) })
                }
                item { Spacer(Modifier.height(4.dp)) }
                item { PrimaryButton("Request an order") { go("dealer/order") } }
                item { SecondaryButton("Ledger & invoices") { go("dealer/ledger") } }
                item { SecondaryButton("Sales to retailers") { go("dealer/secondary") } }
            }
        }
    }
}

@Composable
fun DealerLedgerScreen(api: Api, back: () -> Unit, openWeb: (String) -> Unit) {
    var tab by remember { mutableIntStateOf(0) }
    val ledger = rememberLoader { api.get("dealer/ledger/") }
    val invoices = rememberLoader { api.get("dealer/invoices/") }
    Page("Ledger & invoices", back, eyebrow = "My dealer account", actions = { HeaderAction("PDF / CSV", Icons.Filled.Download) { openWeb("/my-ledger/") } }) {
        Segmented(listOf("Statement", "Invoices"), tab, { tab = it })
        if (tab == 0) LoadState(ledger) { d ->
            val rows = d.getJSONArray("rows").objects().reversed()
            LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
                item {
                    KpiRow({ Kpi("Opening", rupees(d.str("opening")), "start of period", modifier = it) },
                        { Kpi("Closing balance", rupees(d.str("closing")), "positive = you owe", if ((d.str("closing").toBigDecimalOrNull()?.signum() ?: 0) > 0) KpiTone.Warn else KpiTone.Good, it) })
                }
                item { Spacer(Modifier.height(8.dp)) }
                items(rows) { r ->
                    val dr = r.str("debit").toBigDecimalOrNull()?.signum() ?: 0
                    ListRow("${r.str("type")} · ${r.str("ref")}", prettyDate(r.str("date")) + (if (r.str("note").isNotBlank()) " · ${r.str("note")}" else ""),
                        if (dr > 0) rupees(r.str("debit"), true) else "−" + rupees(r.str("credit"), true), "bal ${rupees(r.str("balance"))}",
                        icon = if (dr > 0) Icons.Filled.NorthEast else Icons.Filled.SouthWest, tone = if (dr > 0) Tone(Danger, Color(0xFFFDECEC)) else Tone(Good, Color(0xFFE9F6F1)),
                        trailingColor = if (dr > 0) Ink else Good)
                }
                if (rows.isEmpty()) item { Empty("Invoices and payments will show here.", Icons.AutoMirrored.Filled.ReceiptLong, "No entries yet") }
            }
        } else LoadState(invoices) { d ->
            val rows = d.getJSONArray("invoices").objects()
            LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
                items(rows) { r ->
                    val unpaid = r.str("unpaid").toBigDecimalOrNull()?.signum() ?: 0
                    ListRow(r.str("number"), "${prettyDate(r.str("date"))} · due ${prettyDate(r.str("due_date"))}", rupees(r.str("total"), true),
                        if (unpaid > 0) "unpaid ${rupees(r.str("unpaid"))}" else "", chip = if (unpaid > 0) "Unpaid" else "Paid", chipTone = if (unpaid > 0) Warn else Good,
                        icon = Icons.AutoMirrored.Filled.ReceiptLong, tone = moduleTone("finance")) { openWeb("/pdf/invoice/${r.getInt("id")}/") }
                }
                if (rows.isEmpty()) item { Empty("Your Sistech invoices will show here.", Icons.AutoMirrored.Filled.ReceiptLong, "No invoices yet") }
            }
        }
    }
}

@Composable
fun DealerOrderScreen(api: Api, back: () -> Unit) {
    val scope = rememberCoroutineScope()
    val products = rememberLoader { api.get("dealer/products/").getJSONArray("products").objects() }
    var refresh by remember { mutableIntStateOf(0) }
    val mine = rememberLoader(refresh) { api.get("dealer/orders/").getJSONArray("requests").objects() }
    val qtys = remember { mutableStateMapOf<Int, String>() }
    var days by remember { mutableIntStateOf(3) }
    var note by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf<String?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    Page("Request an order", back, eyebrow = "My dealer account") {
        LoadState(products) { list ->
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(vertical = 12.dp)) {
                item { Text("Enter quantities for what you need. Rates are today's list rates before GST; Sistech confirms the order.", style = MaterialTheme.typography.bodySmall) }
                items(list) { p ->
                    val id = p.getInt("id")
                    SectionCard {
                        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(p.str("name"), style = MaterialTheme.typography.labelLarge)
                                Text(if (p.str("rate").isNotBlank()) "${rupees(p.str("rate"), true)} / ${p.str("unit")}" else "Rate on confirmation", style = MaterialTheme.typography.bodySmall)
                            }
                            OutlinedTextField(qtys[id] ?: "", { v -> qtys[id] = v.filter { it.isDigit() || it == '.' }.take(9) }, label = { Text(p.str("unit")) },
                                singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.width(120.dp))
                        }
                    }
                }
                item {
                    val total = list.sumOf { p ->
                        val q = qtys[p.getInt("id")]?.toBigDecimalOrNull() ?: java.math.BigDecimal.ZERO
                        val r = p.str("rate").toBigDecimalOrNull() ?: java.math.BigDecimal.ZERO
                        val g = p.str("gst_rate").toBigDecimalOrNull() ?: java.math.BigDecimal.ZERO
                        q * r * (java.math.BigDecimal.ONE + g.movePointLeft(2))
                    }
                    SectionCard {
                        Text("Needed in $days day${if (days == 1) "" else "s"} · ${LocalDate.now().plusDays(days.toLong())}", style = MaterialTheme.typography.labelLarge)
                        Slider(days.toFloat(), { days = it.toInt() }, valueRange = 0f..30f, steps = 29)
                        OutlinedTextField(note, { note = it.take(300) }, label = { Text("Note for Sistech (site, unloading…)") }, modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(10.dp))
                        Text("Estimated incl. GST: ${rupees(total.toPlainString())}", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(10.dp))
                        Button(shape = RoundedCornerShape(12.dp), onClick = {
                            busy = true; err = null; msg = null
                            scope.launch {
                                try {
                                    val lines = JSONArray()
                                    qtys.filterValues { (it.toBigDecimalOrNull()?.signum() ?: 0) > 0 }.forEach { (id, q) -> lines.put(JSONObject().put("product", id).put("quantity", q)) }
                                    val r = api.post("dealer/orders/", JSONObject().put("required_by", LocalDate.now().plusDays(days.toLong()).toString()).put("lines", lines).put("note", note))
                                    msg = "Request ${r.getJSONObject("request").str("number")} sent."
                                    qtys.clear(); note = ""; refresh++
                                } catch (e: Exception) { err = e.message } finally { busy = false }
                            }
                        }, enabled = !busy && qtys.values.any { (it.toBigDecimalOrNull()?.signum() ?: 0) > 0 }, modifier = Modifier.fillMaxWidth().height(50.dp)) { Text("Send request") }
                        msg?.let { Text(it, color = Good, modifier = Modifier.padding(top = 8.dp)) }
                        err?.let { ErrorBox(it) }
                    }
                }
                item { SectionTitle("My requests") }
                item {
                    LoadState(mine) { rows ->
                        Column {
                            rows.forEach { r ->
                                val order = r.optJSONObject("order")
                                ListRow(r.str("number"), "needed by ${prettyDate(r.str("required_by"))}" + (order?.let { " · order ${it.str("number")} (${it.str("status")})" } ?: "") +
                                    (if (r.str("reason").isNotBlank()) " · ${r.str("reason")}" else ""), rupees(r.str("estimate")), chip = r.str("status_label"), chipTone = statusTone(r.str("status")))
                            }
                            if (rows.isEmpty()) Empty("No requests yet.")
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun DealerSecondaryScreen(api: Api, back: () -> Unit) {
    val scope = rememberCoroutineScope()
    var refresh by remember { mutableIntStateOf(0) }
    val data = rememberLoader(refresh) { api.get("dealer/secondary/") }
    val products = rememberLoader { api.get("dealer/products/").getJSONArray("products").objects() }
    var retailer by remember { mutableStateOf<JSONObject?>(null) }
    var shop by remember { mutableStateOf("") }
    var product by remember { mutableStateOf<JSONObject?>(null) }
    var quantity by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf<String?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    Page("Sales to retailers", back, eyebrow = "My dealer account") {
        LoadState(data) { d ->
            val retailers = d.getJSONArray("retailers").objects()
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(vertical = 12.dp)) {
                item {
                    SectionCard("Add a sale") {
                        Picker("Retailer", retailers, retailer, { it.str("name") }) { retailer = it }
                        if (retailer == null) OutlinedTextField(shop, { shop = it.take(120) }, label = { Text("Shop name (if not in the list)") }, modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(8.dp))
                        Picker("Product", products.data.orEmpty(), product, { it.str("name") }) { product = it }
                        OutlinedTextField(quantity, { v -> quantity = v.filter { it.isDigit() || it == '.' } }, label = { Text("Quantity (${product?.str("unit") ?: "bags / MT"})") },
                            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(10.dp))
                        Button(shape = RoundedCornerShape(12.dp), onClick = {
                            err = null; msg = null
                            scope.launch {
                                try {
                                    val b = JSONObject().put("product", product!!.getInt("id")).put("quantity", quantity).put("date", LocalDate.now().toString())
                                    if (retailer != null) b.put("retailer", retailer!!.getInt("id")) else b.put("retailer_name", shop)
                                    api.post("dealer/secondary/", b)
                                    msg = "Saved."; quantity = ""; shop = ""; refresh++
                                } catch (e: Exception) { err = e.message }
                            }
                        }, enabled = product != null && quantity.isNotBlank() && (retailer != null || shop.isNotBlank()), modifier = Modifier.fillMaxWidth().height(50.dp)) { Text("Save") }
                        msg?.let { Text(it, color = Good) }
                        err?.let { ErrorBox(it) }
                    }
                }
                item { SectionTitle("My entries") }
                items(d.getJSONArray("entries").objects()) { e ->
                    ListRow(e.str("buyer"), "${prettyDate(e.str("date"))} · ${e.str("product")}", "${qty(e.str("quantity"))} ${e.str("unit")}",
                        chip = if (e.str("status") == "cancelled") "Cancelled" else "", chipTone = Danger)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun <T> Picker(label: String, options: List<T>, selected: T?, text: (T) -> String, onPick: (T?) -> Unit) {
    var open by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(open, { open = it }) {
        OutlinedTextField(selected?.let(text) ?: "", {}, readOnly = true, label = { Text(label) }, placeholder = { Text("Choose") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(open) },
            modifier = Modifier.menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable).fillMaxWidth())
        ExposedDropdownMenu(open, { open = false }) {
            DropdownMenuItem(text = { Text("—") }, onClick = { onPick(null); open = false })
            options.forEach { o -> DropdownMenuItem(text = { Text(text(o)) }, onClick = { onPick(o); open = false }) }
        }
    }
}
