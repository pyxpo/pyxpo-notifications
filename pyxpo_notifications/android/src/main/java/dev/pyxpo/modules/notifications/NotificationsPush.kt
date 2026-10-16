package dev.pyxpo.modules.notifications

import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Remote push with Firebase Cloud Messaging. The app's Firebase settings come from `google-services.json` through
 * the `pyxpo_notifications.plugin` config plugin; without them the token calls fail and nothing else changes.
 */
object NotificationsPush {
    const val TOKEN_EVENT = "Notifications.token"
    private val main = Handler(Looper.getMainLooper())
    private val handled = HashSet<String>()

    private fun configured(c: Context): String? =
        if (FirebaseApp.getApps(c).isEmpty()) "Firebase is not configured: add [\"pyxpo_notifications.plugin\", " +
            "{\"googleServicesFile\": \"./google-services.json\"}] to [tool.pyxpo] plugins" else null

    fun token(c: Context, reply: (Any?, String?) -> Unit) {
        configured(c)?.let { reply(null, it); return }
        FirebaseMessaging.getInstance().token.addOnCompleteListener { t ->
            if (t.isSuccessful && t.result != null) reply(mapOf("type" to "android", "data" to t.result), null)
            else reply(null, "Could not get the FCM token: ${t.exception?.message ?: "unknown error"}")
        }
    }

    fun unregister(c: Context, reply: (Any?, String?) -> Unit) {
        configured(c)?.let { reply(null, it); return }
        FirebaseMessaging.getInstance().deleteToken().addOnCompleteListener { t ->
            if (t.isSuccessful) reply(null, null)
            else reply(null, "Could not delete the FCM token: ${t.exception?.message ?: "unknown error"}")
        }
    }

    /** FCM issued a new token (first start, reinstall, data cleared, token deleted). Any thread. */
    fun newToken(token: String) {
        main.post { NotificationsCore.ctx?.get()?.emit(TOKEN_EVENT, mapOf("type" to "android", "data" to token)) }
    }

    /**
     * A message for this app. Notification messages while the app is open and all data messages come here; FCM
     * itself shows notification messages while the app is in the background. Runs on FCM's worker thread, which
     * waits (at most 5 seconds) while the app's handler decides.
     */
    fun received(c: Context, m: RemoteMessage) {
        val n = m.notification
        val data = m.data
        val title = n?.title ?: data["title"]
        val body = n?.body ?: data["body"]
        val content = JSONObject().put("data", JSONObject(data as Map<*, *>))
        title?.let { content.put("title", it) }
        body?.let { content.put("body", it) }
        if (n?.sound != null) content.put("sound", "default")
        n?.color?.let { content.put("color", it) }
        val trigger = JSONObject().put("type", "push").put("remoteMessage", JSONObject(remoteMessage(m)))
        (n?.channelId ?: data["channelId"])?.let { trigger.put("channelId", it) }
        val req = JSONObject().put("identifier", m.messageId ?: UUID.randomUUID().toString())
            .put("content", content).put("trigger", trigger)
        // A data message without a title or body is for the app only: it is never shown.
        val silent = title == null && body == null
        val latch = CountDownLatch(1)
        main.post {
            if (silent) {
                NotificationsCore.deliverSilently(req)
                latch.countDown()
            } else {
                NotificationsCore.deliver(c, req) { latch.countDown() }
            }
        }
        latch.await(5, TimeUnit.SECONDS)
    }

    private fun remoteMessage(m: RemoteMessage): Map<String, Any?> {
        val n = m.notification
        return mapOf(
            "messageId" to m.messageId, "from" to m.from, "to" to m.to, "collapseKey" to m.collapseKey,
            "messageType" to m.messageType, "sentTime" to m.sentTime.toDouble(), "ttl" to m.ttl,
            "priority" to m.priority, "originalPriority" to m.originalPriority, "data" to m.data,
            "notification" to n?.let {
                mapOf("title" to it.title, "body" to it.body, "channelId" to it.channelId, "color" to it.color,
                    "icon" to it.icon, "imageUrl" to it.imageUrl?.toString(), "sound" to it.sound, "tag" to it.tag,
                    "clickAction" to it.clickAction)
            })
    }

    /**
     * The app was opened by a tap on a notification FCM showed (the launch intent carries the message's data and
     * `google.message_id`). Each message counts once. Main thread.
     */
    fun tapped(intent: Intent?) {
        val extras = intent?.extras ?: return
        val id = extras.getString("google.message_id") ?: return
        if (!handled.add(id)) return
        val data = JSONObject()
        for (k in extras.keySet()) {
            if (k.startsWith("google.") || k.startsWith("gcm.") || k == "from" || k == "collapse_key") continue
            (extras.get(k) as? String)?.let { data.put(k, it) }
        }
        val content = JSONObject().put("data", data)
        data.optString("title").takeIf { it.isNotEmpty() }?.let { content.put("title", it) }
        data.optString("body").takeIf { it.isNotEmpty() }?.let { content.put("body", it) }
        val remote = JSONObject().put("messageId", id).put("from", extras.getString("from"))
            .put("data", JSONObject(data.toString()))
        val req = JSONObject().put("identifier", id).put("content", content)
            .put("trigger", JSONObject().put("type", "push").put("remoteMessage", remote))
        NotificationsCore.responded(req, extras.getLong("google.sent_time", System.currentTimeMillis()))
    }
}

/** Receives FCM tokens and messages (declared in the module's manifest). */
class NotificationsMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) = NotificationsPush.newToken(token)

    override fun onMessageReceived(message: RemoteMessage) = NotificationsPush.received(applicationContext, message)
}
