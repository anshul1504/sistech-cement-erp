package com.sistech.erp

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.sistech.erp.data.Api
import com.sistech.erp.data.Session
import com.sistech.erp.data.objects
import com.sistech.erp.data.str
import com.sistech.erp.ui.*
import kotlinx.coroutines.launch
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val session = Session(applicationContext)
        val api = Api(session)
        setContent { SistechTheme { SistechApp(api, session) } }
    }
}

private data class Tab(val route: String, val label: String, val icon: ImageVector, val selected: ImageVector)

private val tabs = listOf(
    Tab("home", "Home", Icons.Outlined.Home, Icons.Filled.Home),
    Tab("erp", "ERP", Icons.Outlined.GridView, Icons.Filled.GridView),
    Tab("work", "Work", Icons.Outlined.NotificationsNone, Icons.Filled.Notifications),
    Tab("account", "Account", Icons.Outlined.AccountCircle, Icons.Filled.AccountCircle),
)

/** One app for everyone: after sign-in every screen follows this login's roles (dealer, sales, every ERP module). */
@Composable
fun SistechApp(api: Api, session: Session) {
    var signedIn by remember { mutableStateOf(session.token != null) }
    var me by remember { mutableStateOf<JSONObject?>(null) }
    var modules by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var waiting by remember { mutableIntStateOf(0) }
    var loadError by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    DisposableEffect(session) { session.onSignedOut = { signedIn = false; me = null }; onDispose { session.onSignedOut = null } }

    fun loadMe() {
        loadError = null
        scope.launch {
            try {
                me = api.get("me/")
                modules = api.get("modules/").getJSONArray("modules").objects()
                waiting = runCatching { api.get("work/").optInt("total") }.getOrDefault(0)
            } catch (e: Exception) { loadError = e.message }
        }
    }
    LaunchedEffect(signedIn) { if (signedIn) loadMe() }

    if (!signedIn) { LoginScreen(api, session) { signedIn = true }; return }
    val user = me
    if (user == null) {
        Box(Modifier.fillMaxSize().background(Surface).statusBarsPadding().padding(24.dp)) {
            loadError?.let { ErrorBox(it) { loadMe() } } ?: CircularProgressIndicator(color = Navy)
        }
        return
    }

    val nav = rememberNavController()
    val back: () -> Unit = { nav.popBackStack() }
    val go: (String) -> Unit = { route ->
        if (tabs.any { it.route == route }) nav.navigate(route) { popUpTo(nav.graph.findStartDestination().id) { saveState = true }; launchSingleTop = true; restoreState = true }
        else nav.navigate(route)
    }
    val openWeb: (String) -> Unit = { nav.navigate("web?path=" + Uri.encode(it)) }
    val current = nav.currentBackStackEntryAsState().value?.destination?.route

    Scaffold(containerColor = Surface, contentWindowInsets = WindowInsets(0),
        bottomBar = {
            NavigationBar(containerColor = Color.White, tonalElevation = 0.dp) {
                tabs.forEach { t ->
                    val on = current == t.route
                    NavigationBarItem(selected = on, onClick = { go(t.route) },
                        icon = {
                            BadgedBox(badge = { if (t.route == "work" && waiting > 0) Badge(containerColor = Danger) { Text("$waiting", color = Color.White) } }) {
                                Icon(if (on) t.selected else t.icon, t.label)
                            }
                        },
                        label = { Text(t.label, style = MaterialTheme.typography.labelMedium) },
                        colors = NavigationBarItemDefaults.colors(selectedIconColor = Navy, selectedTextColor = Navy, indicatorColor = GoldSoft,
                            unselectedIconColor = Ink3, unselectedTextColor = Ink3))
                }
            }
        }) { pad ->
        NavHost(nav, startDestination = "home", modifier = Modifier.padding(pad)) {
            composable("home") { HomeScreen(api, user, modules, go, openWeb) }
            composable("erp") { ModulesScreen(modules, openWeb) }
            composable("work") { WorkScreen(api, openWeb) }
            composable("account") { AccountScreen(api, user, openWeb) { scope.launch { api.logout() } } }
            composable("dealer") { DealerHomeScreen(api, back, go) }
            composable("dealer/order") { DealerOrderScreen(api, back) }
            composable("dealer/ledger") { DealerLedgerScreen(api, back, openWeb) }
            composable("dealer/secondary") { DealerSecondaryScreen(api, back) }
            composable("sales") { SalesHomeScreen(api, back, openWeb) }
            composable("sales/customers") { CustomersScreen(api, back, openWeb) }
            composable("sales/requests") { DealerRequestsScreen(api, back, openWeb) }
            composable("sales/orders") { OrdersScreen(api, back, openWeb) }
            composable("web?path={path}", arguments = listOf(navArgument("path") { type = NavType.StringType; defaultValue = "/" })) { entry ->
                ErpWebScreen(api, entry.arguments?.getString("path") ?: "/", back)
            }
        }
    }
}

@Composable
fun AccountScreen(api: Api, me: JSONObject, openWeb: (String) -> Unit, signOut: () -> Unit) {
    val u = me.getJSONObject("user")
    val roles = me.optJSONArray("roles")?.let { r -> (0 until r.length()).map { r.getString(it) } }.orEmpty()
    LazyColumn(Modifier.fillMaxSize().background(Surface), contentPadding = PaddingValues(bottom = 24.dp)) {
        item {
            Hero("My account", u.str("name"), u.str("email").ifBlank { "@" + u.str("username") }) {
                if (roles.isNotEmpty()) Text(roles.joinToString(" · "), style = MaterialTheme.typography.labelLarge.copy(color = Gold), modifier = Modifier.padding(top = 8.dp))
            }
        }
        item {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                ActionTile(Icons.Filled.Person, "Profile & security", "Password, signed-in devices, contacts") { openWeb("/accounts/profile/") }
                ActionTile(Icons.Filled.SupportAgent, "Help & support", "Raise a ticket, see replies", tint = Good) { openWeb("/support/") }
                ActionTile(Icons.AutoMirrored.Filled.Logout, "Sign out", "This phone only", tint = Danger) { signOut() }
                Panel {
                    Text("SERVER", style = MaterialTheme.typography.labelSmall)
                    Text(api.baseUrl, style = MaterialTheme.typography.bodyMedium)
                    Text("Sistech ERP app ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp))
                }
            }
        }
    }
}
