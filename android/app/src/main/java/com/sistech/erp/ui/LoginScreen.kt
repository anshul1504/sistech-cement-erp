package com.sistech.erp.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sistech.erp.R
import com.sistech.erp.data.Api
import com.sistech.erp.data.Session
import kotlinx.coroutines.launch

/** Same look as the web sign-in: navy brand panel with the logo card, then the form. Password first, then the email code when asked. */
@Composable
fun LoginScreen(api: Api, session: Session, onSignedIn: () -> Unit) {
    val scope = rememberCoroutineScope()
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var otpEmail by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun run(block: suspend () -> Unit) {
        busy = true; error = null
        scope.launch { try { block() } catch (e: Exception) { error = e.message } finally { busy = false } }
    }
    fun signIn() = run {
        val r = api.login(username, password)
        if (r.optBoolean("otp_required")) otpEmail = r.optString("email")
        else { session.token = r.getString("token"); onSignedIn() }
    }
    fun verifyCode() = run { session.token = api.verify(code).getString("token"); onSignedIn() }

    val field = OutlinedTextFieldDefaults.colors(focusedBorderColor = Navy, unfocusedBorderColor = Line, focusedContainerColor = Color.White,
        unfocusedContainerColor = Color.White, focusedLabelColor = Navy, cursorColor = Navy)

    StatusBarIcons(onDark = true)
    Column(Modifier.fillMaxSize().background(Surface).imePadding().verticalScroll(rememberScrollState())) {
        // Brand panel
        Column(Modifier.fillMaxWidth().background(Brush.verticalGradient(listOf(Navy, Navy2))).statusBarsPadding().padding(start = 24.dp, end = 24.dp, top = 28.dp, bottom = 56.dp)) {
            Box(Modifier.clip(RoundedCornerShape(16.dp)).background(Color.White).padding(horizontal = 18.dp, vertical = 12.dp)) {
                Image(painterResource(R.drawable.logo_full), "Sistech Cement & TMT Pvt. Ltd.", Modifier.width(150.dp))
            }
            Box(Modifier.padding(top = 2.dp).width(150.dp + 36.dp).height(4.dp).background(Gold, RoundedCornerShape(bottomStart = 4.dp, bottomEnd = 4.dp)))
            Spacer(Modifier.height(26.dp))
            Text("SISTECH CEMENT & TMT PVT. LTD.", style = MaterialTheme.typography.labelSmall.copy(color = Color(0xFFC9D6EA)))
            Text(buildAnnotatedString {
                append("Cement and TMT operations, ")
                withStyle(SpanStyle(color = Gold)) { append("in one Sistech ERP.") }
            }, style = MaterialTheme.typography.headlineMedium.copy(color = Color.White, fontSize = 28.sp, lineHeight = 34.sp), modifier = Modifier.padding(top = 6.dp))
        }
        // Form card
        Column(Modifier.padding(horizontal = 16.dp).offset(y = (-32).dp).clip(RoundedCornerShape(20.dp)).background(Color.White).border(1.dp, Line, RoundedCornerShape(20.dp)).padding(22.dp)) {
            Box(Modifier.size(44.dp).clip(RoundedCornerShape(12.dp)).background(Color(0xFFEEF3FB)), contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.Lock, null, tint = Navy, modifier = Modifier.size(20.dp))
            }
            Text(if (otpEmail == null) "Welcome back." else "Check your email", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(top = 14.dp))
            Text(if (otpEmail == null) "Sign in with your Sistech account to continue." else "We sent a 6-digit code to $otpEmail.",
                style = MaterialTheme.typography.bodyMedium.copy(color = Ink2), modifier = Modifier.padding(top = 2.dp, bottom = 18.dp))
            if (otpEmail == null) {
                OutlinedTextField(username, { username = it.trim() }, label = { Text("Email address or username") }, singleLine = true, colors = field, shape = RoundedCornerShape(12.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next), modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(password, { password = it }, label = { Text("Password") }, singleLine = true, visualTransformation = PasswordVisualTransformation(),
                    colors = field, shape = RoundedCornerShape(12.dp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { if (!busy && username.isNotBlank() && password.isNotBlank()) signIn() }), modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(20.dp))
                PrimaryButton(if (busy) "Signing in…" else "Sign in to Sistech ERP", enabled = !busy && username.isNotBlank() && password.isNotBlank()) { signIn() }
            } else {
                OutlinedTextField(code, { v -> code = v.filter { it.isDigit() }.take(6) }, label = { Text("6-digit verification code") }, singleLine = true,
                    colors = field, shape = RoundedCornerShape(12.dp), textStyle = MaterialTheme.typography.titleLarge.copy(letterSpacing = 6.sp),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { if (!busy && code.length == 6) verifyCode() }), modifier = Modifier.fillMaxWidth())
                Spacer(Modifier.height(20.dp))
                PrimaryButton(if (busy) "Checking…" else "Verify and continue", enabled = !busy && code.length == 6) { verifyCode() }
                TextButton(onClick = { otpEmail = null; code = ""; password = "" }, modifier = Modifier.align(Alignment.CenterHorizontally)) {
                    Text("Use a different account", style = MaterialTheme.typography.labelLarge.copy(color = Brand))
                }
            }
            error?.let { ErrorBox(it) }
            Row(Modifier.fillMaxWidth().padding(top = 18.dp), verticalAlignment = Alignment.CenterVertically) {
                HorizontalDivider(Modifier.weight(1f), color = Line)
                Text("  SISTECH STAFF & PARTNERS  ", style = MaterialTheme.typography.labelSmall)
                HorizontalDivider(Modifier.weight(1f), color = Line)
            }
            Text("New here? Ask the ERP administrator for an account.", style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.align(Alignment.CenterHorizontally).padding(top = 10.dp))
        }
        Text("Design & developed by The Webfix", style = MaterialTheme.typography.bodySmall, modifier = Modifier.align(Alignment.CenterHorizontally).padding(bottom = 16.dp))
    }
    Box(Modifier.fillMaxWidth().windowInsetsTopHeight(WindowInsets.statusBars).background(Navy2))
}
