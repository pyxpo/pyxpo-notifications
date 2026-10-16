package dev.pyxpo.modules.notifications

import android.app.ActivityManager
import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import dev.pyxpo.moduleapi.PxModuleContext
import org.json.JSONArray
import org.json.JSONObject
import java.lang.ref.WeakReference
import java.util.Calendar

/**
 * Process-wide notification state, shared by the module, the alarm and boot receivers, and the tap trampoline.
 * Scheduled requests are kept in SharedPreferences so a reboot (or an app update) can schedule them again.
 * Main thread only.
 */
object NotificationsCore {
    const val DEFAULT_ACTION = "expo.modules.notifications.actions.DEFAULT"
    const val FALLBACK_CHANNEL = "pyxpo_notifications_fallback"
    const val EXTRA_REQUEST = "pyxpo.request"
    const val EXTRA_DATE = "pyxpo.date"
    const val ACTION_FIRE = "dev.pyxpo.notifications.FIRE"

    /** The running app, while it runs (events go there). */
    var ctx: WeakReference<PxModuleContext>? = null
    var handlerSet = false
    var lastResponse: Map<String, Any?>? = null

    private val main = Handler(Looper.getMainLooper())
    private var nextDecision = 1
    private val pending = HashMap<Int, (JSONObject?) -> Unit>()

    fun prefs(c: Context): SharedPreferences = c.getSharedPreferences("pyxpo_notifications", Context.MODE_PRIVATE)

    private fun nm(c: Context) = c.getSystemService(NotificationManager::class.java)

    // scheduling

    /** Shows `req` now (no trigger, or only a channelId) or schedules it; returns its identifier. */
    fun schedule(c: Context, req: JSONObject): String {
        val id = req.getString("identifier")
        val trigger = req.optJSONObject("trigger")
        cancel(c, id)
        if (trigger == null || !trigger.has("type")) {
            deliver(c, req) {}
            return id
        }
        val now = System.currentTimeMillis()
        val next = if (trigger.optString("type") == "timeInterval") now + intervalMs(trigger)
            else nextFire(trigger, now) ?: throw IllegalArgumentException("the trigger never fires")
        store(c, JSONObject().put("request", req).put("next", next))
        setAlarm(c, id, next)
        return id
    }

    fun cancel(c: Context, id: String) {
        val all = stored(c)
        if (all.has(id)) {
            all.remove(id)
            prefs(c).edit().putString("scheduled", all.toString()).apply()
        }
        c.getSystemService(AlarmManager::class.java).cancel(alarmIntent(c, id))
    }

    fun cancelAll(c: Context) {
        for (id in stored(c).keys().asSequence().toList()) cancel(c, id)
    }

    /** The scheduled requests (the request dicts Python sent). */
    fun scheduled(c: Context): List<JSONObject> {
        val all = stored(c)
        return all.keys().asSequence().mapNotNull { all.optJSONObject(it)?.optJSONObject("request") }.toList()
    }

    private fun stored(c: Context): JSONObject =
        try { JSONObject(prefs(c).getString("scheduled", "{}") ?: "{}") } catch (_: Exception) { JSONObject() }

    private fun store(c: Context, entry: JSONObject) {
        val all = stored(c)
        all.put(entry.getJSONObject("request").getString("identifier"), entry)
        prefs(c).edit().putString("scheduled", all.toString()).apply()
    }

    private fun intervalMs(t: JSONObject) = (t.optDouble("seconds", 1.0) * 1000).toLong().coerceAtLeast(1)

    private fun alarmIntent(c: Context, id: String): PendingIntent {
        // The data URI makes each identifier its own PendingIntent.
        val i = Intent(c, NotificationAlarmReceiver::class.java).setAction(ACTION_FIRE)
            .setData(Uri.parse("pyxpo-notification:" + Uri.encode(id))).putExtra("id", id)
        return PendingIntent.getBroadcast(c, 0, i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun setAlarm(c: Context, id: String, at: Long) {
        val am = c.getSystemService(AlarmManager::class.java)
        val pi = alarmIntent(c, id)
        // Exact alarms need SCHEDULE_EXACT_ALARM (opt-in) on Android 12+; without it the system may delay a little.
        if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        } else {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        }
    }

    /** The alarm for `id` went off: show it, then schedule its next time or forget it. Calls `done` when finished. */
    fun fire(c: Context, id: String, done: () -> Unit) {
        val entry = stored(c).optJSONObject(id) ?: run { done(); return }
        val req = entry.getJSONObject("request")
        val t = req.getJSONObject("trigger")
        val now = System.currentTimeMillis()
        val next = when (t.optString("type")) {
            "timeInterval" -> if (t.optBoolean("repeats")) catchUp(entry.optLong("next", now), intervalMs(t), now) else null
            "date" -> null
            else -> nextFire(t, maxOf(now, entry.optLong("next", now)))
        }
        if (next != null) {
            store(c, entry.put("next", next))
            setAlarm(c, id, next)
        } else {
            cancel(c, id)
        }
        deliver(c, req, done)
    }

    private fun catchUp(from: Long, step: Long, now: Long): Long {
        var n = from + step
        if (n <= now) n += ((now - n) / step + 1) * step
        return n
    }

    /** After a reboot or an app update the alarms are gone: set them again (missed one-shot ones show now). */
    fun reschedule(c: Context) {
        val now = System.currentTimeMillis()
        val all = stored(c)
        for (id in all.keys().asSequence().toList()) {
            val entry = all.optJSONObject(id) ?: continue
            val t = entry.optJSONObject("request")?.optJSONObject("trigger") ?: continue
            var next = entry.optLong("next", now)
            if (next <= now) {
                val repeats = t.optString("type") != "date" && (t.optString("type") != "timeInterval" || t.optBoolean("repeats"))
                if (!repeats) { fire(c, id) {}; continue }
                next = if (t.optString("type") == "timeInterval") catchUp(next, intervalMs(t), now)
                    else nextFire(t, now) ?: continue
                store(c, entry.put("next", next))
            }
            setAlarm(c, id, next)
        }
    }

    /** When a calendar-style trigger (date, daily, weekly, monthly, yearly) next fires after `from`, in local time. */
    fun nextFire(t: JSONObject, from: Long): Long? {
        val type = t.optString("type")
        if (type == "date") return t.optDouble("date").toLong().takeIf { it > from }
        if (type == "timeInterval") return from + intervalMs(t)
        val hour = t.optInt("hour")
        val minute = t.optInt("minute")
        val cal = Calendar.getInstance().apply { timeInMillis = from }
        fun at(k: Calendar): Long {
            k.set(Calendar.HOUR_OF_DAY, hour); k.set(Calendar.MINUTE, minute)
            k.set(Calendar.SECOND, 0); k.set(Calendar.MILLISECOND, 0)
            return k.timeInMillis
        }
        when (type) {
            "daily", "weekly" -> repeat(9) {
                val ok = type == "daily" || cal.get(Calendar.DAY_OF_WEEK) == t.optInt("weekday")  // 1 is Sunday in both
                val time = at(cal.clone() as Calendar)
                if (ok && time > from) return time
                cal.add(Calendar.DAY_OF_MONTH, 1)
            }
            "monthly", "yearly" -> {
                val day = t.optInt("day")
                cal.set(Calendar.DAY_OF_MONTH, 1)
                if (type == "yearly") {
                    // Months without that day (February 30, February 29 outside leap years) are skipped.
                    repeat(10) {
                        val k = cal.clone() as Calendar
                        k.set(Calendar.MONTH, t.optInt("month"))
                        if (day <= k.getActualMaximum(Calendar.DAY_OF_MONTH)) {
                            k.set(Calendar.DAY_OF_MONTH, day)
                            val time = at(k)
                            if (time > from) return time
                        }
                        cal.add(Calendar.YEAR, 1)
                    }
                } else {
                    repeat(50) {
                        if (day <= cal.getActualMaximum(Calendar.DAY_OF_MONTH)) {
                            val k = cal.clone() as Calendar
                            k.set(Calendar.DAY_OF_MONTH, day)
                            val time = at(k)
                            if (time > from) return time
                        }
                        cal.add(Calendar.MONTH, 1)
                    }
                }
            }
        }
        return null
    }

    // showing

    private fun foreground(): Boolean {
        val info = ActivityManager.RunningAppProcessInfo()
        ActivityManager.getMyMemoryState(info)
        return info.importance == ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND
    }

    /**
     * A notification is due. In the background it is posted. While the app is open, Python gets it first and its
     * handler (if set) decides within 3 seconds whether it shows, as in Expo.
     */
    fun deliver(c: Context, req: JSONObject, done: () -> Unit) {
        val date = System.currentTimeMillis()
        val app = ctx?.get()
        if (app == null || !foreground()) { post(c, req, date); done(); return }
        val n = mapOf("date" to date.toDouble(), "request" to requestMap(req))
        if (!handlerSet) {
            app.emit("Notifications.received", mapOf("notification" to n))
            done()
            return
        }
        val id = nextDecision++
        pending[id] = { b ->
            if (b != null && (b.optBoolean("banner") || b.optBoolean("list"))) post(c, req, date)
            done()
        }
        app.emit("Notifications.received", mapOf("notification" to n, "decisionId" to id))
        main.postDelayed({ decide(id, null) }, 3000)
    }

    fun decide(id: Int, behavior: JSONObject?) {
        pending.remove(id)?.invoke(behavior)
    }

    fun fallbackChannel(c: Context) {
        if (Build.VERSION.SDK_INT < 26 || nm(c).getNotificationChannel(FALLBACK_CHANNEL) != null) return
        nm(c).createNotificationChannel(
            NotificationChannel(FALLBACK_CHANNEL, "Miscellaneous", NotificationManager.IMPORTANCE_HIGH))
    }

    private fun color(s: String): Int? = try {
        // '#RRGGBBAA' (web order) becomes Android's '#AARRGGBB'.
        Color.parseColor(if (s.length == 9) "#" + s.substring(7) + s.substring(1, 7) else s)
    } catch (_: IllegalArgumentException) { null }

    @Suppress("DEPRECATION")
    private fun post(c: Context, req: JSONObject, date: Long) {
        val id = req.getString("identifier")
        val content = req.optJSONObject("content") ?: JSONObject()
        val trigger = req.optJSONObject("trigger")
        var channel = trigger?.optString("channelId")?.takeIf { it.isNotEmpty() }
        val b = if (Build.VERSION.SDK_INT >= 26) {
            if (channel == null || nm(c).getNotificationChannel(channel) == null) {
                fallbackChannel(c)
                channel = FALLBACK_CHANNEL
            }
            Notification.Builder(c, channel)
        } else {
            Notification.Builder(c).apply {
                setPriority(when (content.optString("priority")) {
                    "min" -> Notification.PRIORITY_MIN; "low" -> Notification.PRIORITY_LOW
                    "high" -> Notification.PRIORITY_HIGH; "max" -> Notification.PRIORITY_MAX
                    else -> Notification.PRIORITY_DEFAULT
                })
                if (content.has("sound")) setDefaults(Notification.DEFAULT_SOUND)
            }
        }
        b.setSmallIcon(R.drawable.pyxpo_notification_icon)
        str(content, "title")?.let { b.setContentTitle(it) }
        str(content, "body")?.let { b.setContentText(it); b.setStyle(Notification.BigTextStyle().bigText(it)) }
        str(content, "subtitle")?.let { b.setSubText(it) }
        str(content, "color")?.let { s -> color(s)?.let { b.setColor(it) } }
        if (content.has("badge") && !content.isNull("badge")) b.setNumber(content.optInt("badge"))
        b.setAutoCancel(content.optBoolean("autoDismiss", true))
        b.setOngoing(content.optBoolean("sticky", false))
        b.setWhen(date).setShowWhen(true)
        b.setExtras(android.os.Bundle().apply {
            putString(EXTRA_REQUEST, req.toString())
            putLong(EXTRA_DATE, date)
        })
        val tap = Intent(c, NotificationResponseActivity::class.java)
            .setData(Uri.parse("pyxpo-notification:" + Uri.encode(id)))
            .putExtra(EXTRA_REQUEST, req.toString()).putExtra(EXTRA_DATE, date)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        b.setContentIntent(PendingIntent.getActivity(c, 0, tap,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
        // Without POST_NOTIFICATIONS (Android 13+) the system drops it silently, as it does for any app.
        try { nm(c).notify(id, 0, b.build()) } catch (_: SecurityException) {}
    }

    private fun str(o: JSONObject, k: String): String? = if (o.isNull(k)) null else o.optString(k).takeIf { it.isNotEmpty() }

    /** The user tapped a notification (the trampoline activity calls this). */
    fun responded(req: JSONObject, date: Long) {
        val r = mapOf("notification" to mapOf("date" to date.toDouble(), "request" to requestMap(req)),
            "actionIdentifier" to DEFAULT_ACTION)
        lastResponse = r
        ctx?.get()?.emit("Notifications.response", mapOf("response" to r))
    }

    fun presented(c: Context): List<Map<String, Any?>> =
        nm(c).activeNotifications.filter { it.packageName == c.packageName }.map { sbn ->
            val extras = sbn.notification.extras
            val req = extras.getString(EXTRA_REQUEST)?.let { try { JSONObject(it) } catch (_: Exception) { null } }
                ?: JSONObject().put("identifier", sbn.tag ?: sbn.id.toString()).put("content", JSONObject()
                    .put("title", extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: JSONObject.NULL)
                    .put("body", extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: JSONObject.NULL))
            mapOf("date" to extras.getLong(EXTRA_DATE, sbn.postTime).toDouble(), "request" to requestMap(req))
        }

    /** Expo's NotificationRequest shape. */
    fun requestMap(req: JSONObject): Map<String, Any?> {
        val content = plain(req.optJSONObject("content") ?: JSONObject()).toMutableMap()
        for (k in listOf("title", "subtitle", "body", "badge", "sound")) if (!content.containsKey(k)) content[k] = null
        if (content["data"] == null) content["data"] = emptyMap<String, Any?>()
        return mapOf("identifier" to req.optString("identifier"), "content" to content,
            "trigger" to req.optJSONObject("trigger")?.let { plain(it) })
    }

    fun plain(o: JSONObject): Map<String, Any?> = o.keys().asSequence().associateWith { value(o.opt(it)) }

    private fun value(v: Any?): Any? = when (v) {
        JSONObject.NULL, null -> null
        is JSONObject -> plain(v)
        is JSONArray -> (0 until v.length()).map { value(v.opt(it)) }
        else -> v
    }

    // channels (Android 8+)

    /** Expo's AndroidImportance from NotificationManager's importance. */
    fun expoImportance(i: Int): Int = when (i) {
        NotificationManager.IMPORTANCE_UNSPECIFIED -> 1
        in 0..5 -> i + 2
        else -> 0
    }

    private fun androidImportance(expo: Int): Int =
        if (expo <= 1) NotificationManager.IMPORTANCE_DEFAULT else (expo - 2).coerceIn(0, 5)

    fun setChannel(c: Context, id: String, o: JSONObject): Map<String, Any?>? {
        if (Build.VERSION.SDK_INT < 26) return null
        val ch = NotificationChannel(id, o.getString("name"), androidImportance(o.optInt("importance", 5)))
        str(o, "description")?.let { ch.description = it }
        if (o.has("showBadge")) ch.setShowBadge(o.optBoolean("showBadge"))
        if (o.has("bypassDnd")) ch.setBypassDnd(o.optBoolean("bypassDnd"))
        if (o.has("enableLights")) ch.enableLights(o.optBoolean("enableLights"))
        str(o, "lightColor")?.let { s -> color(s)?.let { ch.lightColor = it } }
        if (o.has("enableVibrate")) ch.enableVibration(o.optBoolean("enableVibrate"))
        o.optJSONArray("vibrationPattern")?.let { a -> ch.vibrationPattern = LongArray(a.length()) { a.optLong(it) } }
        when (o.optInt("lockscreenVisibility", 0)) {
            1 -> ch.lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            2 -> ch.lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            3 -> ch.lockscreenVisibility = Notification.VISIBILITY_SECRET
        }
        if (o.has("sound") && o.isNull("sound")) ch.setSound(null, null)
        str(o, "groupId")?.let { g -> if (nm(c).getNotificationChannelGroup(g) != null) ch.group = g }
        nm(c).createNotificationChannel(ch)
        return channel(c, id)
    }

    fun channel(c: Context, id: String): Map<String, Any?>? =
        if (Build.VERSION.SDK_INT < 26) null else nm(c).getNotificationChannel(id)?.let(::channelMap)

    fun channels(c: Context): List<Map<String, Any?>> =
        if (Build.VERSION.SDK_INT < 26) emptyList() else nm(c).notificationChannels.map(::channelMap)

    private fun channelMap(ch: NotificationChannel): Map<String, Any?> {
        val argb = ch.lightColor
        return mapOf(
            "id" to ch.id, "name" to ch.name?.toString(), "importance" to expoImportance(ch.importance),
            "bypassDnd" to ch.canBypassDnd(), "description" to ch.description, "groupId" to ch.group,
            "lightColor" to String.format("#%06X%02X", argb and 0xFFFFFF, (argb ushr 24) and 0xFF),
            "lockscreenVisibility" to when (ch.lockscreenVisibility) {
                Notification.VISIBILITY_PUBLIC -> 1; Notification.VISIBILITY_PRIVATE -> 2
                Notification.VISIBILITY_SECRET -> 3; else -> 0
            },
            "showBadge" to ch.canShowBadge(), "sound" to if (ch.sound != null) "default" else null,
            "vibrationPattern" to ch.vibrationPattern?.toList(), "enableLights" to ch.shouldShowLights(),
            "enableVibrate" to ch.shouldVibrate())
    }
}
