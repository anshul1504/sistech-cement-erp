package com.sistech.erp.data

import com.sistech.erp.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.CookieHandler
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.HttpURLConnection
import java.net.URL

/** Error the screens can show as-is (server messages are already written for people). */
class ApiException(message: String, val status: Int = 0) : Exception(message)

/**
 * Small JSON client for /api/v1/ (see docs/API.md). Every call runs on the IO dispatcher.
 * Sign-in keeps the server session cookie between the password and email-code steps; all other calls use the bearer token.
 */
class Api(private val session: Session) {
    val baseUrl: String = BuildConfig.BASE_URL.trimEnd('/')

    init {
        if (CookieHandler.getDefault() == null) CookieHandler.setDefault(CookieManager(null, CookiePolicy.ACCEPT_ORIGINAL_SERVER))
    }

    suspend fun get(path: String, params: Map<String, String> = emptyMap()): JSONObject {
        val query = params.filterValues { it.isNotBlank() }.entries.joinToString("&") { (k, v) ->
            "${java.net.URLEncoder.encode(k, "UTF-8")}=${java.net.URLEncoder.encode(v, "UTF-8")}"
        }
        return call("GET", if (query.isEmpty()) path else "$path?$query", null)
    }

    suspend fun post(path: String, body: JSONObject = JSONObject()): JSONObject = call("POST", path, body)

    private suspend fun call(method: String, path: String, body: JSONObject?): JSONObject = withContext(Dispatchers.IO) {
        val conn = (URL("$baseUrl/api/v1/$path").openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15_000
            readTimeout = 30_000
            setRequestProperty("Accept", "application/json")
            session.token?.let { setRequestProperty("Authorization", "Bearer $it") }
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
        }
        try {
            if (body != null) conn.outputStream.use { it.write(body.toString().toByteArray()) }
            val status = conn.responseCode
            val text = (if (status < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            val json = runCatching { JSONObject(text) }.getOrElse { JSONObject().put("ok", false).put("error", "The server sent an unexpected reply ($status).") }
            if (status == 401 && session.token != null && !path.startsWith("auth/")) session.clear()
            if (status >= 400 || !json.optBoolean("ok")) throw ApiException(json.optString("error", "Something went wrong ($status)."), status)
            json
        } catch (e: IOException) {
            throw ApiException("Can't reach the Sistech server. Check your internet connection.")
        } finally {
            conn.disconnect()
        }
    }

    // ---------- Sign-in ----------
    suspend fun login(username: String, password: String): JSONObject =
        post("auth/login/", JSONObject().put("username", username).put("password", password).put("device", deviceName()))

    suspend fun verify(code: String): JSONObject = post("auth/verify/", JSONObject().put("code", code))

    suspend fun logout() {
        runCatching { post("auth/logout/") }
        session.clear()
    }

    /** One-time link that opens the web ERP signed in (60 s). Returns an absolute URL. */
    suspend fun webSession(next: String): String = baseUrl + post("auth/web-session/", JSONObject().put("next", next)).getString("url")

    private fun deviceName() = "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}".take(80)
}

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

fun JSONObject.str(key: String): String = if (isNull(key)) "" else optString(key)
