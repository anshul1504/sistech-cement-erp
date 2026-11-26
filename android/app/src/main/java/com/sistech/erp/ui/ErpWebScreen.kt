package com.sistech.erp.ui

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.webkit.*
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import com.sistech.erp.data.Api
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.compose.ui.unit.dp

/**
 * Full web ERP inside the app. The app's token becomes a one-time web session (POST auth/web-session/), so every module
 * opens already signed in. Downloads (PDF, CSV, Excel) go to the phone's Downloads; file inputs open the picker; links
 * to other sites open in the browser; Back walks the page history before leaving.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun ErpWebScreen(api: Api, path: String, back: () -> Unit) {
    val context = LocalContext.current
    var url by remember(path) { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var progress by remember { mutableIntStateOf(0) }
    var title by remember { mutableStateOf("Sistech ERP") }
    var web by remember { mutableStateOf<WebView?>(null) }
    var pending by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }
    var file by remember { mutableStateOf<FileState?>(null) }
    val scope = rememberCoroutineScope()
    fun fetch(u: String, ua: String?) {
        file = FileState(URLUtil.guessFileName(u, null, null), busy = true)
        scope.launch {
            file = try { val (name, uri, mime) = saveDownload(context, u, ua); FileState(name, uri = uri, mime = mime) }
                   catch (e: Exception) { FileState(file?.name ?: "file", error = e.message ?: "Download failed.") }
        }
    }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        pending?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
        pending = null
    }

    LaunchedEffect(path) {
        error = null
        url = try { api.webSession(path) } catch (e: Exception) { error = e.message; null }
    }
    BackHandler { if (web?.canGoBack() == true) web?.goBack() else back() }

    StatusBarIcons(onDark = true)
    // The web ERP brings its own header (menu, modules, search, bell, create); the app adds only the status-bar inset and progress.
    Column(Modifier.fillMaxSize().background(androidx.compose.ui.graphics.Color.White).statusBarsPadding()) {
        if (progress in 1..99) LinearProgressIndicator(progress = { progress / 100f }, modifier = Modifier.fillMaxWidth(), color = Gold)
        error?.let { ErrorBox(it) { url = null; error = null } }
        val start = url
        Box(Modifier.fillMaxSize()) {
        if (start != null) AndroidView(modifier = Modifier.fillMaxSize(), factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.setSupportMultipleWindows(false)
                settings.allowFileAccess = false
                settings.userAgentString = settings.userAgentString + " SistechApp/1.0"
                CookieManager.getInstance().setAcceptCookie(true)
                val host = Uri.parse(api.baseUrl).host
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        val u = request.url
                        if (u.host != host) { ctx.startActivity(Intent(Intent.ACTION_VIEW, u)); return true }
                        val p = u.path.orEmpty()
                        if (p.startsWith("/pdf/") || u.getQueryParameter("export") != null || u.getQueryParameter("download") != null || p.endsWith("/file/")) {
                            fetch(u.toString(), view.settings.userAgentString); return true
                        }
                        return false
                    }
                    override fun onPageFinished(view: WebView, u: String) { title = view.title?.substringBefore(" · ")?.ifBlank { null } ?: "Sistech ERP" }
                }
                webChromeClient = object : WebChromeClient() {
                    override fun onProgressChanged(view: WebView, p: Int) { progress = p }
                    override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                        pending?.onReceiveValue(null)
                        pending = callback
                        return try { picker.launch(params.createIntent()); true } catch (e: Exception) { pending = null; false }
                    }
                }
                setDownloadListener { u, ua, _, _, _ -> fetch(u, ua) }
                loadUrl(start)
                web = this
            }
        })
        file?.let { f -> DownloadCard(f, onOpen = { f.uri?.let { open(context, it, f.mime) } }, onClose = { file = null; if (web?.url == null || web?.canGoBack() == false) back() }) }
        }
    }
}

data class FileState(val name: String, val busy: Boolean = false, val uri: Uri? = null, val mime: String? = null, val error: String? = null)

/** Bottom sheet-style card for a download: progress → "Saved to Downloads" with Open, or the error. */
@Composable
private fun BoxScope.DownloadCard(f: FileState, onOpen: () -> Unit, onClose: () -> Unit) {
    Column(Modifier.align(androidx.compose.ui.Alignment.BottomCenter).padding(16.dp).fillMaxWidth().card(20, elevation = 10).padding(18.dp)) {
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            IconTile(if (f.error != null) Icons.Filled.ErrorOutline else if (f.busy) Icons.Filled.Download else Icons.Filled.TaskAlt,
                if (f.error != null) Tone(Danger, androidx.compose.ui.graphics.Color(0xFFFDECEC)) else if (f.busy) Tone(Navy, GoldSoft) else SuccessTone, 42)
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(f.name, style = MaterialTheme.typography.labelLarge.copy(color = Ink), maxLines = 1, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                Text(f.error ?: if (f.busy) "Downloading…" else "Saved to Downloads", style = MaterialTheme.typography.bodySmall.copy(color = if (f.error != null) Danger else Ink3))
            }
            if (f.busy) CircularProgressIndicator(Modifier.size(22.dp), color = Navy, strokeWidth = 2.dp)
        }
        if (!f.busy) Row(Modifier.padding(top = 14.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            SecondaryButton("Close", Modifier.weight(1f)) { onClose() }
            if (f.uri != null) PrimaryButton("Open", modifier = Modifier.weight(1f), icon = Icons.Filled.OpenInNew) { onOpen() }
        }
    }
}

private fun open(context: Context, uri: Uri, mime: String?) {
    val i = Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime ?: "application/pdf").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    try { context.startActivity(i) } catch (e: Exception) { Toast.makeText(context, "No app on this phone can open this file.", Toast.LENGTH_SHORT).show() }
}

/** Downloads with the web session's cookie and saves to the phone's Downloads (Android 10+) or the app's shareable cache (older). */
private suspend fun saveDownload(context: Context, url: String, ua: String?): Triple<String, Uri, String> = withContext(Dispatchers.IO) {
    val conn = (java.net.URL(url).openConnection() as java.net.HttpURLConnection).apply {
        connectTimeout = 15_000; readTimeout = 60_000
        setRequestProperty("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
        setRequestProperty("User-Agent", ua ?: "SistechApp")
    }
    try {
        if (conn.responseCode >= 400) throw java.io.IOException("The server refused this download (${conn.responseCode}).")
        val mime = conn.contentType?.substringBefore(';')?.trim() ?: "application/octet-stream"
        // Server sends `inline; filename="…"` (URLUtil only reads attachment), so take the name ourselves.
        val cd = conn.getHeaderField("Content-Disposition").orEmpty()
        val name = Regex("filename=\"?([^\";]+)").find(cd)?.groupValues?.get(1)?.replace(Regex("[\\\\/:*?<>|]"), "-")?.trim()
            ?: URLUtil.guessFileName(url, cd, mime)
        val bytes = conn.inputStream.use { it.readBytes() }
        val uri = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
            val values = android.content.ContentValues().apply {
                put(android.provider.MediaStore.Downloads.DISPLAY_NAME, name)
                put(android.provider.MediaStore.Downloads.MIME_TYPE, mime)
            }
            val r = context.contentResolver
            val u = r.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: throw java.io.IOException("Could not save to Downloads.")
            r.openOutputStream(u)?.use { it.write(bytes) }
            u
        } else {
            val f = java.io.File(java.io.File(context.cacheDir, "docs").apply { mkdirs() }, name)
            f.writeBytes(bytes)
            androidx.core.content.FileProvider.getUriForFile(context, context.packageName + ".files", f)
        }
        Triple(name, uri, mime)
    } catch (e: java.io.IOException) {
        throw Exception(e.message?.takeIf { it.startsWith("The server") || it.startsWith("Could not") } ?: "Download failed. Check your internet connection.")
    } finally { conn.disconnect() }
}
