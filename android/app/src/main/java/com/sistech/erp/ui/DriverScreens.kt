package com.sistech.erp.ui

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.sistech.erp.data.Api
import com.sistech.erp.data.objects
import com.sistech.erp.data.str
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter

// ---------- Driver: home section, trip list, trip detail, POD, road exception ----------

private val tripSteps = listOf("planned" to "Planned", "gate_in" to "At gate", "loading" to "Loading", "weighed" to "Weighed", "gate_out" to "On the road", "delivered" to "Delivered")
private fun stepIndex(status: String) = when (status) {
    "planned" -> 0; "gate_in", "tared" -> 1; "loading", "loaded" -> 2; "weighed" -> 3; "gate_out" -> 4; "delivered" -> 5; else -> 0
}

fun prettyTime(iso: String): String = runCatching {
    OffsetDateTime.parse(iso).atZoneSameInstant(java.time.ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("d MMM, h:mm a"))
}.getOrDefault(iso)

/** Home content for a driver login: their numbers, the trip on the road (big card), next trips. */
@Composable
fun DriverHomeSection(api: Api, go: (String) -> Unit) {
    val summary = rememberLoader { api.get("driver/summary/") }
    val trips = rememberLoader { api.get("driver/trips/").getJSONArray("trips").objects() }
    LoadState(summary) { d ->
        val drv = d.getJSONObject("driver")
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            KpiRow({ Kpi("On the road", d.optInt("on_road").toString(), "trip in transit", if (d.optInt("on_road") > 0) KpiTone.Warn else KpiTone.Normal, it) },
                { Kpi("At the plant", d.optInt("at_plant").toString(), "gate / loading", modifier = it) })
            KpiRow({ Kpi("Delivered today", d.optInt("delivered_today").toString(), "${d.optInt("delivered_month")} this month", KpiTone.Good, it) },
                { Kpi("Licence", if (drv.optBoolean("licence_valid")) "Valid" else "Expired", "till ${prettyDate(drv.str("licence_valid_until"))}",
                    if (drv.optBoolean("licence_valid")) KpiTone.Normal else KpiTone.Danger, it) })
        }
    }
    LoadState(trips) { rows ->
        val live = rows.firstOrNull { it.str("status") == "gate_out" }
        if (live != null) {
            SectionHeader("Trip on the road")
            LiveTripCard(live) { go("driver/trip/${live.getInt("id")}") }
        }
        SectionHeader("My trips", "All") { go("driver") }
        val rest = rows.filter { it !== live }.take(3)
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) { rest.forEach { TripRow(it) { go("driver/trip/${it.getInt("id")}") } } }
        if (rows.isEmpty()) Panel { Empty("Trips the plant plans for you will show here.", Icons.Outlined.LocalShipping, "No trips yet") }
    }
}

@Composable
private fun LiveTripCard(t: JSONObject, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(androidx.compose.ui.graphics.Brush.linearGradient(listOf(Navy, Brand))).clickable(onClick = onClick).padding(18.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(42.dp).clip(RoundedCornerShape(12.dp)).background(Gold), contentAlignment = Alignment.Center) { Icon(Icons.Filled.LocalShipping, null, tint = Navy) }
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(t.str("number"), style = MaterialTheme.typography.labelMedium.copy(color = Color(0xFFC9D6EA)))
                Text(t.str("customer"), style = MaterialTheme.typography.titleMedium.copy(color = Color.White, fontWeight = FontWeight.Bold), maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            StatusPill("In transit", Gold)
        }
        Text(t.str("quantity"), style = MaterialTheme.typography.bodyMedium.copy(color = Color.White), modifier = Modifier.padding(top = 12.dp))
        Text("${t.str("vehicle")} · ${t.str("city").ifBlank { t.str("site_name") }}" + (if (t.str("gate_out_at").isNotBlank()) " · left ${prettyTime(t.str("gate_out_at"))}" else ""),
            style = MaterialTheme.typography.bodySmall.copy(color = Color(0xFFC9D6EA)))
        Row(Modifier.padding(top = 14.dp).clip(CircleShape).background(Gold).padding(horizontal = 14.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.TaskAlt, null, tint = Navy, modifier = Modifier.size(16.dp))
            Text("  Open trip · record delivery", style = MaterialTheme.typography.labelLarge.copy(color = Navy))
        }
    }
}

@Composable
private fun TripRow(t: JSONObject, onClick: () -> Unit) {
    val st = t.str("status")
    ListRow(t.str("customer"), "${t.str("number")} · ${t.str("quantity")}", if (st == "delivered" && t.str("delivered_at").isNotBlank()) prettyTime(t.str("delivered_at")) else prettyDate(t.str("planned_for")),
        t.str("vehicle"), chip = t.str("status_label"), chipTone = statusTone(st), icon = if (st == "delivered") Icons.Filled.TaskAlt else Icons.Filled.LocalShipping,
        tone = if (st == "delivered") Tone(Good, Color(0xFFE9F6F1)) else if (st == "gate_out") Tone(Navy, GoldSoft) else Tone(Brand, Color(0xFFEEF3FB)), trailingColor = Ink2, onClick = onClick)
}

/** All trips of this driver: on the road, at the plant, delivered. */
@Composable
fun DriverTripsScreen(api: Api, back: () -> Unit, go: (String) -> Unit) {
    var tab by remember { mutableIntStateOf(0) }
    var refresh by remember { mutableIntStateOf(0) }
    val trips = rememberLoader(refresh) { api.get("driver/trips/").getJSONArray("trips").objects() }
    Page("My trips", back, eyebrow = "Driver", onRefresh = { refresh++ }, refreshing = trips.loading && trips.data != null) {
        Segmented(listOf("Active", "Delivered"), tab, { tab = it })
        LoadState(trips) { rows ->
            val shown = rows.filter { if (tab == 0) it.str("status") != "delivered" && it.str("status") != "returned" else it.str("status") == "delivered" || it.str("status") == "returned" }
            LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
                items(shown) { t -> TripRow(t) { go("driver/trip/${t.getInt("id")}") } }
                if (shown.isEmpty()) item { Empty(if (tab == 0) "No open trips right now." else "Delivered trips of the last 30 days show here.", Icons.Outlined.LocalShipping) }
            }
        }
    }
}

/** One trip: progress, where to deliver (call / navigate), what is loaded, papers, exceptions, and the actions while in transit. */
@Composable
fun DriverTripScreen(api: Api, id: Int, back: () -> Unit, go: (String) -> Unit) {
    val context = LocalContext.current
    var refresh by remember { mutableIntStateOf(0) }
    val data = rememberLoader(refresh) { api.get("driver/trips/$id/") }
    Page(data.data?.getJSONObject("trip")?.str("number") ?: "Trip", back, eyebrow = "Driver · trip", onRefresh = { refresh++ }, refreshing = data.loading && data.data != null) {
        LoadState(data) { d ->
            val t = d.getJSONObject("trip")
            val st = t.str("status")
            Column(Modifier.fillMaxSize()) {
            LazyColumn(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(top = 14.dp, bottom = 20.dp)) {
                item {
                    Panel {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("CUSTOMER", style = MaterialTheme.typography.labelSmall)
                                Text(t.str("customer"), style = MaterialTheme.typography.titleLarge.copy(color = Ink), maxLines = 2)
                            }
                            StatusPill(t.str("status_label"), statusTone(st))
                        }
                        TripProgress(st)
                    }
                }
                item {
                    SectionCard("Deliver to") {
                        InfoLine(Icons.Outlined.Place, "Address", t.str("ship_to").ifBlank { t.str("city") })
                        InfoLine(Icons.Outlined.Person, "Contact", t.str("contact"))
                        InfoLine(Icons.Outlined.Info, "Instructions", t.str("instructions"))
                        Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            val phone = t.str("phone").ifBlank { t.str("alt_phone") }
                            if (phone.isNotBlank()) SecondaryButton("Call", Modifier.weight(1f), Icons.Filled.Call, Good) { open(context, Intent(Intent.ACTION_DIAL, Uri.parse("tel:$phone"))) }
                            val addr = t.str("ship_to").ifBlank { t.str("city") }
                            if (addr.isNotBlank()) SecondaryButton("Navigate", Modifier.weight(1f), Icons.Filled.Navigation, Brand) {
                                open(context, Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/maps/dir/?api=1&destination=" + Uri.encode(addr))))
                            }
                        }
                    }
                }
                item {
                    SectionCard("Load & papers") {
                        InfoLine(Icons.Outlined.Inventory2, "Loaded", t.str("quantity"))
                        InfoLine(Icons.Outlined.LocalShipping, "Vehicle", t.str("vehicle") + (if (t.str("transporter").isNotBlank()) " · ${t.str("transporter")}" else ""))
                        InfoLine(Icons.Outlined.ReceiptLong, "Invoice", t.str("invoice"))
                        InfoLine(Icons.Outlined.Description, "LR / GR", t.str("lr_number"))
                        InfoLine(Icons.Outlined.Lock, "Seal", t.str("seal"))
                        InfoLine(Icons.Outlined.Scale, "Net weight", if (t.str("net_kg").isNotBlank()) "${kg(t.str("net_kg"))} kg" else "")
                        InfoLine(Icons.Outlined.Factory, "From", "${t.str("site_name")} (${t.str("site")})")
                    }
                }
                t.optJSONObject("pod")?.let { pod ->
                    item {
                        SectionCard("Delivery proof") {
                            InfoLine(Icons.Outlined.TaskAlt, "Received by", pod.str("received_by") + (if (pod.str("mobile").isNotBlank()) " · ${pod.str("mobile")}" else ""))
                            InfoLine(Icons.Outlined.Schedule, "Delivered", prettyTime(t.str("delivered_at")))
                            val short = pod.str("shortage").toBigDecimalOrNull()?.signum() ?: 0
                            val dmg = pod.str("damaged").toBigDecimalOrNull()?.signum() ?: 0
                            if (short > 0 || dmg > 0) InfoLine(Icons.Outlined.Warning, "Short / damaged", "${qty(pod.str("shortage"))} / ${qty(pod.str("damaged"))} · ${pod.str("notes")}")
                            if (pod.optBoolean("has_file")) StatusPill("Photo uploaded", Good, Modifier.padding(top = 6.dp))
                        }
                    }
                }
                val incidents = t.getJSONArray("incidents").objects()
                if (incidents.isNotEmpty()) item {
                    SectionCard("Road exceptions") {
                        incidents.forEach { x ->
                            Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.Top) {
                                IconTile(Icons.Filled.ReportProblem, Tone(Danger, Color(0xFFFDECEC)), 34)
                                Column(Modifier.weight(1f).padding(start = 10.dp)) {
                                    Text(x.str("kind_label"), style = MaterialTheme.typography.labelLarge.copy(color = Ink))
                                    Text(x.str("note") + (if (x.str("location").isNotBlank()) " · ${x.str("location")}" else ""), style = MaterialTheme.typography.bodySmall)
                                    if (x.str("decision_note").isNotBlank()) Text("Office: ${x.str("decision_note")}", style = MaterialTheme.typography.bodySmall.copy(color = Ink2))
                                }
                                StatusPill(x.str("status_label"), statusTone(x.str("status")))
                            }
                        }
                    }
                }
            }
            if (t.optBoolean("can_update")) Column(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                PrimaryButton("Record delivery (POD)", icon = Icons.Filled.CameraAlt) { go("driver/trip/$id/pod") }
                SecondaryButton("Report a problem on the road", icon = Icons.Filled.ReportProblem, color = Danger) { go("driver/trip/$id/issue") }
            }
            }
        }
    }
}

@Composable
private fun TripProgress(status: String) {
    val at = stepIndex(status)
    Row(Modifier.fillMaxWidth().padding(top = 16.dp), verticalAlignment = Alignment.Top) {
        tripSteps.forEachIndexed { i, (_, label) ->
            val done = i <= at
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f).height(3.dp).background(if (i == 0) Color.Transparent else if (done) Gold else Line))
                    Box(Modifier.size(if (i == at) 16.dp else 12.dp).clip(CircleShape).background(if (done) Navy else Line)
                        .then(if (i == at) Modifier.border(3.dp, Gold, CircleShape) else Modifier))
                    Box(Modifier.weight(1f).height(3.dp).background(if (i == tripSteps.lastIndex) Color.Transparent else if (i < at) Gold else Line))
                }
                Text(label, style = MaterialTheme.typography.labelMedium.copy(color = if (done) Ink else Ink3, fontWeight = if (i == at) FontWeight.Bold else FontWeight.Medium),
                    maxLines = 2, modifier = Modifier.padding(top = 6.dp), textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            }
        }
    }
}

private fun open(context: Context, intent: Intent) {
    runCatching { context.startActivity(intent) }
}

/** POD: receiver, optional shortage / damage, and a photo of the signed copy (camera or gallery). */
@Composable
fun DriverPodScreen(api: Api, id: Int, back: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var receiver by remember { mutableStateOf("") }
    var mobile by remember { mutableStateOf("") }
    var shortage by remember { mutableStateOf("") }
    var damaged by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var photo by remember { mutableStateOf<ByteArray?>(null) }
    var preview by remember { mutableStateOf<Bitmap?>(null) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf(false) }
    val shot = remember { File(context.cacheDir, "pod").apply { mkdirs() }.let { File(it, "pod_$id.jpg") } }
    val shotUri = remember { FileProvider.getUriForFile(context, context.packageName + ".files", shot) }

    fun take(uri: Uri) {
        scope.launch {
            val r = withContext(Dispatchers.IO) { compress(context, uri) }
            if (r == null) err = "Could not read that photo. Try again." else { photo = r.first; preview = r.second; err = null }
        }
    }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok -> if (ok) take(shotUri) }
    val gallery = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri -> if (uri != null) take(uri) }
    val issue = (shortage.toBigDecimalOrNull()?.signum() ?: 0) > 0 || (damaged.toBigDecimalOrNull()?.signum() ?: 0) > 0

    Page("Record delivery", back, eyebrow = "Proof of delivery") {
        LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(top = 14.dp, bottom = 28.dp)) {
            if (done) {
                item {
                    Panel {
                        Empty("The office can see the POD and photo now. Thank you!", Icons.Filled.TaskAlt, "Delivery recorded", SuccessTone)
                        PrimaryButton("Back to trip") { back() }
                    }
                }
                return@LazyColumn
            }
            item {
                SectionCard("1 · Photo of signed delivery copy") {
                    val p = preview
                    if (p != null) {
                        Image(p.asImageBitmap(), "POD photo", Modifier.fillMaxWidth().height(220.dp).clip(RoundedCornerShape(12.dp)), contentScale = ContentScale.Crop)
                        Text("${(photo?.size ?: 0) / 1024} KB · ready to upload", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
                    } else {
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color(0xFFF3F6FB)).border(1.dp, Line, RoundedCornerShape(12.dp))
                            .clickable { camera.launch(shotUri) }.padding(vertical = 28.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                            Icon(Icons.Filled.PhotoCamera, null, tint = Navy, modifier = Modifier.size(34.dp))
                            Text("Tap to take a photo", style = MaterialTheme.typography.labelLarge.copy(color = Navy), modifier = Modifier.padding(top = 6.dp))
                            Text("LR / invoice copy with receiver's sign & stamp", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        SecondaryButton(if (preview != null) "Retake" else "Camera", Modifier.weight(1f), Icons.Filled.PhotoCamera) { camera.launch(shotUri) }
                        SecondaryButton("Gallery", Modifier.weight(1f), Icons.Filled.PhotoLibrary) { gallery.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
                    }
                }
            }
            item {
                SectionCard("2 · Who received it") {
                    Field(receiver, { receiver = it.take(80) }, "Receiver name", leading = Icons.Outlined.Person)
                    Spacer(Modifier.height(8.dp))
                    Field(mobile, { v -> mobile = v.filter { it.isDigit() }.take(10) }, "Receiver mobile (optional)", keyboard = KeyboardType.Phone, leading = Icons.Outlined.Phone)
                }
            }
            item {
                SectionCard("3 · Shortage or damage (if any)") {
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Field(shortage, { v -> shortage = v.filter { it.isDigit() || it == '.' }.take(9) }, "Short", Modifier.weight(1f), keyboard = KeyboardType.Decimal)
                        Field(damaged, { v -> damaged = v.filter { it.isDigit() || it == '.' }.take(9) }, "Damaged", Modifier.weight(1f), keyboard = KeyboardType.Decimal)
                    }
                    Spacer(Modifier.height(8.dp))
                    Field(notes, { notes = it.take(200) }, if (issue) "What happened (needed for the claim)" else "Notes (optional)", singleLine = false, minLines = 2)
                }
            }
            item {
                err?.let { ErrorBox(it) }
                PrimaryButton("Submit delivery", enabled = receiver.isNotBlank() && photo != null && (!issue || notes.isNotBlank()), busy = busy, icon = Icons.Filled.CloudUpload) {
                    busy = true; err = null
                    scope.launch {
                        try {
                            api.postForm("driver/trips/$id/deliver/", mapOf("received_by" to receiver, "receiver_mobile" to mobile, "shortage" to shortage.ifBlank { "0" },
                                "damaged" to damaged.ifBlank { "0" }, "notes" to notes), "photo", photo)
                            shot.delete(); done = true
                        } catch (e: Exception) { err = e.message } finally { busy = false }
                    }
                }
                if (photo == null) Text("A photo of the signed copy is required.", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
            }
        }
    }
}

/** Downscale to ≤1600 px and JPEG-encode (≈200–600 KB) so the upload is quick on a weak network. */
private fun compress(context: Context, uri: Uri): Pair<ByteArray, Bitmap>? = runCatching {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1600) sample *= 2
    val bmp = context.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample }) } ?: return null
    val out = ByteArrayOutputStream()
    bmp.compress(Bitmap.CompressFormat.JPEG, 82, out)
    out.toByteArray() to bmp
}.getOrNull()

private val issueIcons = mapOf("breakdown" to Icons.Filled.CarCrash, "delay" to Icons.Filled.Schedule, "diversion" to Icons.Filled.AltRoute, "refused" to Icons.Filled.DoNotDisturbOn)

/** Report a breakdown, delay, diversion or refused load. Diversion / refusal wait for an office decision. */
@Composable
fun DriverIssueScreen(api: Api, id: Int, back: () -> Unit) {
    val scope = rememberCoroutineScope()
    val data = rememberLoader { api.get("driver/trips/$id/") }
    var kind by remember { mutableStateOf<String?>(null) }
    var note by remember { mutableStateOf("") }
    var location by remember { mutableStateOf("") }
    var newAddress by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf<String?>(null) }
    Page("Report a problem", back, eyebrow = "On the road") {
        LoadState(data) { d ->
            val kinds = d.getJSONArray("incident_kinds").objects()
            LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = PaddingValues(top = 14.dp, bottom = 28.dp)) {
                done?.let { msg ->
                    item { Panel { Empty(msg, Icons.Filled.TaskAlt, "Reported to the office", SuccessTone); PrimaryButton("Back to trip") { back() } } }
                    return@LazyColumn
                }
                item { Text("What happened?", style = MaterialTheme.typography.titleMedium.copy(color = Ink)) }
                item {
                    Grid2(kinds.map { k ->
                        { m: Modifier ->
                            val on = kind == k.str("key")
                            Column(m.clip(RoundedCornerShape(14.dp)).background(if (on) GoldSoft else Color.White).border(if (on) 2.dp else 1.dp, if (on) Gold else Line, RoundedCornerShape(14.dp))
                                .clickable { kind = k.str("key") }.padding(14.dp)) {
                                IconTile(issueIcons[k.str("key")] ?: Icons.Filled.ReportProblem, if (k.str("key") in listOf("diversion", "refused")) Tone(Danger, Color(0xFFFDECEC)) else Tone(Warn, GoldSoft), 38)
                                Text(k.str("label"), style = MaterialTheme.typography.labelLarge.copy(color = Ink), maxLines = 3, modifier = Modifier.padding(top = 10.dp))
                            }
                        }
                    })
                }
                if (kind != null) item {
                    SectionCard("Details") {
                        Field(note, { note = it.take(300) }, "Describe what happened", singleLine = false, minLines = 3)
                        Spacer(Modifier.height(8.dp))
                        Field(location, { location = it.take(120) }, "Where are you? (place / highway / km)", leading = Icons.Outlined.Place)
                        if (kind == "diversion") { Spacer(Modifier.height(8.dp)); Field(newAddress, { newAddress = it.take(300) }, "New delivery address", singleLine = false, minLines = 2) }
                        if (kind == "diversion" || kind == "refused") Text("The office must approve this before you change the route or return.", style = MaterialTheme.typography.bodySmall.copy(color = Danger),
                            modifier = Modifier.padding(top = 8.dp))
                    }
                }
                item {
                    err?.let { ErrorBox(it) }
                    PrimaryButton("Send to office", enabled = kind != null && note.isNotBlank() && (kind != "diversion" || newAddress.isNotBlank()), busy = busy, icon = Icons.Filled.Send) {
                        busy = true; err = null
                        scope.launch {
                            try {
                                api.post("driver/trips/$id/incident/", JSONObject().put("kind", kind).put("note", note).put("location", location).put("new_ship_to", newAddress))
                                done = if (kind == "diversion" || kind == "refused") "Wait for the office decision — you'll see it on the trip page." else "The office has been told. Update them when it is sorted."
                            } catch (e: Exception) { err = e.message } finally { busy = false }
                        }
                    }
                }
            }
        }
    }
}
