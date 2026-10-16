package dev.pyxpo.modules.notifications

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import dev.pyxpo.moduleapi.PxModule
import dev.pyxpo.moduleapi.PxModuleContext
import dev.pyxpo.moduleapi.PxReply
import org.json.JSONArray
import java.lang.ref.WeakReference

/**
 * expo-notifications on Android: local notifications posted with NotificationManager and scheduled with
 * AlarmManager (no Firebase). State that outlives the activity (scheduled requests, the last tap) is in
 * [NotificationsCore].
 */
class NotificationsModule : PxModule() {
    override val name = "Notifications"

    override fun call(ctx: PxModuleContext, method: String, args: JSONArray, reply: PxReply) {
        val c = ctx.activity.applicationContext
        val core = NotificationsCore
        core.ctx = WeakReference(ctx)
        when (method) {
            "listen" -> reply(null, null)
            "setHandler" -> { core.handlerSet = args.optBoolean(0); reply(null, null) }
            "presentDecision" -> { core.decide(args.optInt(0, -1), args.optJSONObject(1)); reply(null, null) }
            "getPermissions" -> reply(permission(ctx), null)
            "requestPermissions" -> requestPermissions(ctx, reply)
            "schedule" -> reply(core.schedule(c, args.getJSONObject(0)), null)
            "nextTriggerDate" -> reply(core.nextFire(args.getJSONObject(0), System.currentTimeMillis())?.toDouble(), null)
            "getScheduled" -> reply(core.scheduled(c).map { core.requestMap(it) }, null)
            "cancel" -> { core.cancel(c, args.optString(0)); reply(null, null) }
            "cancelAll" -> { core.cancelAll(c); reply(null, null) }
            "getPresented" -> reply(core.presented(c), null)
            "dismiss" -> { nm(c).cancel(args.optString(0), 0); reply(null, null) }
            "dismissAll" -> { nm(c).cancelAll(); reply(null, null) }
            // Android launchers count notifications themselves; there is no app badge to read or set.
            "getBadge" -> reply(0, null)
            "setBadge" -> reply(false, null)
            "getLastResponse" -> reply(core.lastResponse, null)
            "clearLastResponse" -> { core.lastResponse = null; reply(null, null) }
            "setChannel" -> reply(core.setChannel(c, args.getString(0), args.getJSONObject(1)), null)
            "getChannel" -> reply(core.channel(c, args.getString(0)), null)
            "getChannels" -> reply(core.channels(c), null)
            "deleteChannel" -> {
                if (Build.VERSION.SDK_INT >= 26) nm(c).deleteNotificationChannel(args.getString(0))
                reply(null, null)
            }
            else -> reply(null, "Notifications has no method $method")
        }
    }

    private fun nm(c: Context) = c.getSystemService(NotificationManager::class.java)

    private fun requestPermissions(ctx: PxModuleContext, reply: PxReply) {
        val c = ctx.activity.applicationContext
        // Android 13 shows the prompt only once the app has a channel.
        NotificationsCore.fallbackChannel(c)
        if (Build.VERSION.SDK_INT < 33 || granted(c)) { reply(permission(ctx), null); return }
        ctx.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS)) {
            NotificationsCore.prefs(c).edit().putBoolean("asked", true).apply()
            reply(permission(ctx), null)
        }
    }

    private fun granted(c: Context): Boolean =
        nm(c).areNotificationsEnabled() && (Build.VERSION.SDK_INT < 33 ||
            c.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)

    private fun permission(ctx: PxModuleContext): Map<String, Any?> {
        val a = ctx.activity
        val c = a.applicationContext
        val asked = NotificationsCore.prefs(c).getBoolean("asked", false)
        val (status, canAsk) = when {
            granted(c) -> "granted" to true
            Build.VERSION.SDK_INT >= 33 && !asked -> "undetermined" to true
            Build.VERSION.SDK_INT >= 33 && nm(c).areNotificationsEnabled() ->
                "denied" to a.shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS)
            else -> "denied" to false
        }
        val n = nm(c)
        return mapOf("status" to status, "canAskAgain" to canAsk,
            "android" to mapOf("importance" to NotificationsCore.expoImportance(n.importance),
                "interruptionFilter" to n.currentInterruptionFilter))
    }
}
