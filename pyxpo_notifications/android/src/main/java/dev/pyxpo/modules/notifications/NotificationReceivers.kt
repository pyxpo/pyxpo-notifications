package dev.pyxpo.modules.notifications

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Bundle
import org.json.JSONObject

/** A scheduled notification is due (AlarmManager). */
class NotificationAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra("id") ?: return
        // The app's handler may take up to 3 seconds to decide; keep the receiver alive until then.
        val result = goAsync()
        NotificationsCore.fire(context.applicationContext, id) { result.finish() }
    }
}

/** Alarms do not survive a reboot or an app update: set them again. */
class NotificationBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED, "android.intent.action.QUICKBOOT_POWERON" ->
                NotificationsCore.reschedule(context.applicationContext)
        }
    }
}

/**
 * The notification's tap target: records the response, then opens the app (its launcher activity) and closes.
 * An activity, not a receiver: Android 12+ blocks starting activities from receivers that a notification started.
 */
class NotificationResponseActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val req = intent.getStringExtra(NotificationsCore.EXTRA_REQUEST)?.let {
            try { JSONObject(it) } catch (_: Exception) { null }
        }
        if (req != null && savedInstanceState == null) {
            NotificationsCore.responded(req, intent.getLongExtra(NotificationsCore.EXTRA_DATE, System.currentTimeMillis()))
        }
        packageManager.getLaunchIntentForPackage(packageName)?.let {
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
            startActivity(it)
        }
        finish()
    }
}
