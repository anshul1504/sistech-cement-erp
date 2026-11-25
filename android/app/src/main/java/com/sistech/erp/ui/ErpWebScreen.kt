package com.sistech.erp.ui

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.webkit.*
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import com.sistech.erp.data.Api

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
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        pending?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data))
        pending = null
    }

    LaunchedEffect(path) {
        error = null
        url = try { api.webSession(path) } catch (e: Exception) { error = e.message; null }
    }
    BackHandler { if (web?.canGoBack() == true) web?.goBack() else back() }

    StatusBarIcons(onDark = false)
    // The web ERP brings its own header (menu, modules, search, bell, create); the app adds only the status-bar inset and progress.
    Column(Modifier.fillMaxSize().background(androidx.compose.ui.graphics.Color.White).statusBarsPadding()) {
        if (progress in 1..99) LinearProgressIndicator(progress = { progress / 100f }, modifier = Modifier.fillMaxWidth(), color = Gold)
        error?.let { ErrorBox(it) { url = null; error = null } }
        val start = url
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
                            download(ctx, u.toString(), view.settings.userAgentString, null, null); return true
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
                setDownloadListener { u, ua, disposition, mime, _ -> download(ctx, u, ua, disposition, mime) }
                loadUrl(start)
                web = this
            }
        })
    }
}

private fun download(context: Context, url: String, ua: String?, disposition: String?, mime: String?) {
    val name = URLUtil.guessFileName(url, disposition, mime)
    val req = DownloadManager.Request(Uri.parse(url))
        .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(url) ?: "")
        .addRequestHeader("User-Agent", ua ?: "SistechApp")
        .setTitle(name)
        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
        .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
    (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
    Toast.makeText(context, "Downloading $name", Toast.LENGTH_SHORT).show()
}
