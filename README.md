# pyxpo-notifications

Local notifications for [pyxpo](https://github.com/pyxpo/pyxpo) apps. The API follows
[expo-notifications](https://docs.expo.dev/versions/latest/sdk/notifications/). iOS uses UserNotifications.
Android posts with `NotificationManager` and schedules with `AlarmManager`; scheduled notifications are set again
after a reboot or an app update. Remote push uses FCM on Android and APNs on iOS (see below).

```sh
pip install pyxpo-notifications
```

```toml
[tool.pyxpo]
modules = ["notifications"]

# Optional: exact timing on Android 12+. Without it Android may show a scheduled notification a little late.
[tool.pyxpo.android]
permissions = ["android.permission.SCHEDULE_EXACT_ALARM"]
```

```python
import pyxpo_notifications as Notifications

# Show notifications while the app is open too (without a handler they show only in the background).
Notifications.set_notification_handler({
    "handle_notification": lambda n: {"should_show_banner": True, "should_show_list": True,
                                      "should_play_sound": True, "should_set_badge": False},
})

perm = await Notifications.request_permissions_async()
if perm["granted"]:
    await Notifications.schedule_notification_async({
        "content": {"title": "Tea", "body": "Your tea is ready", "data": {"cup": 2}},
        "trigger": {"type": Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, "seconds": 180},
    })

sub = Notifications.add_notification_response_received_listener(
    lambda r: print("tapped", r["notification"]["request"]["content"]["data"]))
```

## Functions

| Function | Notes |
| --- | --- |
| `request_permissions_async(ios=...)` / `get_permissions_async()` | Expo's permission response, plus `ios` (`status` as `IosAuthorizationStatus`, `allowsAlert`, `allowsBadge`, `allowsSound`, ...) or `android` (`importance`, `interruptionFilter`). iOS options: `allow_alert`, `allow_badge`, `allow_sound`, `allow_provisional`, `allow_critical_alerts`, `provide_app_notification_settings`. A provisional iOS permission has `granted` False and `ios.status == PROVISIONAL`. |
| `use_permissions(get=True, request=False)` | Hook: `(response, request, get)`. |
| `schedule_notification_async({"content", "trigger", "identifier"})` | Returns the identifier. `trigger` None (or only `channel_id`) shows it now. |
| `get_all_scheduled_notifications_async()` | `[{"identifier", "content", "trigger"}]`. |
| `cancel_scheduled_notification_async(id)` / `cancel_all_scheduled_notifications_async()` | |
| `get_next_trigger_date_async(trigger)` | Milliseconds since 1970, or None. |
| `get_presented_notifications_async()` | Shown notifications: `[{"date", "request"}]`. |
| `dismiss_notification_async(id)` / `dismiss_all_notifications_async()` | |
| `set_notification_handler(handler)` | `handle_notification(notification)` (may be async) returns `should_show_banner`, `should_show_list`, `should_play_sound`, `should_set_badge`; optional `handle_success(id)`, `handle_error(id, error)`. It has 3 seconds to answer. |
| `add_notification_received_listener(fn)` | A notification arrived while the app is open. |
| `add_notification_response_received_listener(fn)` | The user tapped one: `{"notification", "actionIdentifier"}` (`DEFAULT_ACTION_IDENTIFIER`). |
| `get_last_notification_response_async()` / `use_last_notification_response()` | The last tap, including the one that launched the app. `clear_last_notification_response_async()` forgets it. |
| `get_badge_count_async()` / `set_badge_count_async(n)` | iOS app icon badge. Android: 0 and False (launchers count notifications). |
| `set_notification_channel_async(id, channel)`, `get_notification_channel_async(id)`, `get_notification_channels_async()`, `delete_notification_channel_async(id)` | Android channels: `name`, `importance` (`AndroidImportance`), `description`, `vibration_pattern`, `enable_vibrate`, `enable_lights`, `light_color`, `lockscreen_visibility`, `show_badge`, `bypass_dnd`, `sound` ('default' or None). None or [] on iOS. |

Notification content: `title`, `subtitle`, `body`, `data` (a JSON-compatible dict), `badge`, `sound` (True for
the default sound); Android also `sticky`, `auto_dismiss`, `priority`, `color`; iOS `interruption_level`.
Android shows a notification on `trigger["channel_id"]`, else on a default channel.

Triggers (`SchedulableTriggerInputTypes`):

| Type | Keys |
| --- | --- |
| `TIME_INTERVAL` | `seconds`, `repeats` (iOS repeats only every 60 seconds or more) |
| `DATE` | `date`: a datetime or milliseconds. A bare datetime or number works as a trigger too. |
| `DAILY` | `hour`, `minute` |
| `WEEKLY` | `weekday` (1 is Sunday), `hour`, `minute` |
| `MONTHLY` | `day`, `hour`, `minute` (months without that day are skipped) |
| `YEARLY` | `month` (0 is January), `day`, `hour`, `minute` |
| `CALENDAR` | iOS only: `year`, `month` (1 is January), `day`, `weekday`, `hour`, `minute`, `second`, ..., `repeats`, `timezone` |

Times are local. camelCase names work too (`scheduleNotificationAsync`, `{"channelId": "alerts"}`).

## Remote push

Android uses FCM, iOS uses APNs. There is no Expo push service: your server sends to the device token directly.

```toml
[tool.pyxpo]
modules = ["notifications"]
plugins = [["pyxpo_notifications.plugin", {"googleServicesFile": "./google-services.json"}]]
```

Plugin options: `googleServicesFile` (from the Firebase console; Android has no FCM token without it), `mode`
(iOS `aps-environment`, "development" or "production"), `enableBackgroundRemoteNotifications` (iOS
`remote-notification` background mode).

| Function | Notes |
| --- | --- |
| `get_device_push_token_async()` | `{"type": "android", "data": <FCM token>}` or `{"type": "ios", "data": <APNs token, hex>}`. |
| `add_push_token_listener(fn)` | Called with the new token when it changes. |
| `unregister_for_notifications_async()` | Android deletes the FCM token; iOS unregisters from APNs. |
| `get_expo_push_token_async()` | Raises `NotSupportedError`. |

A received push goes to the same listeners as a local notification. To send a test push through FCM HTTP v1:

```sh
python tools/send_push.py --key ~/.pyxpo-secrets/service-account.json --token <device token> \
    --title "Hello" --body "from FCM"
```

The `google-services.json` file has no secrets. The service account key sends pushes for the whole project:
keep it out of the app and the repo.

## Not supported yet

- Categories and action buttons, attachments, custom sounds.

## Notes

- iOS: the Simulator cannot pre-grant notifications; `allow_provisional` gives quiet notifications without a prompt.
- Android 13+: the system prompt needs `POST_NOTIFICATIONS` (added by this package). Without the permission the
  system drops notifications silently.
- Android: a notification fired while the app is open goes to Python first; with no handler it is not shown.
