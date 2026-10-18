# pyxpo-notifications

Local and remote notifications for [pyxpo](https://github.com/pyxpo/pyxpo) apps written in Python. The API follows
[expo-notifications](https://docs.expo.dev/versions/latest/sdk/notifications/): ask permission, schedule a notification for
later, decide how to show one while the app is open, react to taps, manage Android channels, and receive push through APNs
(iOS) and Firebase Cloud Messaging (Android). iOS uses UserNotifications. Android posts with `NotificationManager` and
schedules with `AlarmManager`.

<p align="center">
  <img src="https://raw.githubusercontent.com/pyxpo/pyxpo/main/packages/notifications/docs/images/hero.png" width="820" alt="A notification scheduled five seconds ahead after the permission prompt, on Android and iOS">
</p>

```python
import pyxpo_notifications as Notifications

await Notifications.request_permissions_async()
await Notifications.schedule_notification_async({
    "content": {"title": "Tea is ready", "body": "Your 2nd cup", "data": {"cup": 2}},
    "trigger": {"type": Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, "seconds": 180},
})
```

## Features

- **Permission** request and status, with the iOS and Android details (provisional, can-ask-again).
- **Scheduling:** now, after N seconds, at a date, daily, weekly, monthly, yearly, or (iOS) by calendar fields.
- **Foreground handler** that decides banner, list, sound and badge while the app is open.
- **Taps:** listeners, plus the last tap even when it launched the app.
- **Management:** list, cancel and dismiss notifications, and the iOS badge count.
- **Android channels:** importance, vibration, lights, lock screen visibility.
- **Remote push:** device token for APNs and FCM, token-change listener, config plugin for entitlements and Firebase settings.
- **Expo-shaped:** camelCase names (`scheduleNotificationAsync`) and dict keys work.

## Contents

| If you are... | Start here |
| --- | --- |
| Showing a reminder | [Quick start](#quick-start) |
| Asking permission | [Permission](#permission) |
| Picking when it fires | [Triggers](#triggers) |
| Showing it while the app is open | [Foreground handler](#foreground-handler) |
| Reacting to a tap | [Taps](#taps) |
| Setting Android sound and priority | [Android channels](#android-channels) |
| Sending push from a server | [Remote push](#remote-push) |
| Exact names | [API reference](#api-reference) · [Errors](#errors) |
| A real use | [Recipe: daily reminder](#recipe-daily-reminder) |
| Stuck | [Testing](#testing) · [Troubleshooting](#troubleshooting) · [FAQ](#faq) |

## Platform support

| Target | What works |
| --- | --- |
| Android, API 24+ | Everything. Android 13+ shows a permission prompt. Exact alarms are opt-in. |
| iOS 15+ | Everything. Calendar triggers and badge count are iOS only. Push on a real device or a signed simulator build. |
| Desktop window and tests | Not supported: functions raise `NotSupportedError("notifications need iOS or Android")`. Validation errors still raise first. |
| Web page | Not supported. |

Requires Python 3.9+ and `pyxpo>=0.1.0a2`.

## Install

```sh
pip install pyxpo-notifications
```

```toml
[tool.pyxpo]
modules = ["notifications"]
```

Rebuild the app. The package adds `POST_NOTIFICATIONS` and `RECEIVE_BOOT_COMPLETED` on Android. For exact alarm timing on Android 12+ add
the permission yourself:

```toml
[tool.pyxpo.android]
permissions = ["android.permission.SCHEDULE_EXACT_ALARM"]
```

There are no Info.plist texts. For remote push see [Remote push](#remote-push).

## Quick start

```python
from pyxpo import Button, Text, View, component, use_async, use_state
import pyxpo_notifications as Notifications

Notifications.set_notification_handler({
    "handle_notification": lambda n: {"should_show_banner": True, "should_show_list": True,
                                      "should_play_sound": False, "should_set_badge": False},
})


@component
def Reminder():
    taps, set_taps = use_state(0)
    status, set_status = use_state("Not scheduled")

    async def run():
        if taps == 0:
            return
        perm = await Notifications.request_permissions_async()
        if not perm["granted"]:
            set_status("Permission denied")
            return
        nid = await Notifications.schedule_notification_async({
            "content": {"title": "Tea is ready", "body": "Your 2nd cup", "data": {"cup": 2}},
            "trigger": {"type": Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, "seconds": 5},
        })
        set_status("Scheduled %s for 5 seconds from now" % nid[:8])

    use_async(run, [taps])

    return View(Text(status), Button("Notify me in 5 s", lambda: set_taps(taps + 1)))
```

> **Tip:** without a handler, notifications do not show while the app is open, so set one at start-up (above) if you test with the app in front.
> A button handler of `lambda: coroutine()` would never be awaited: count taps and let `use_async` do the work.

<p align="center">
  <img src="https://raw.githubusercontent.com/pyxpo/pyxpo/main/packages/notifications/docs/images/scheduled.png" width="720" alt="Permission granted and one notification scheduled, on Android and iOS">
</p>

## Permission

```python
perm = await Notifications.get_permissions_async()          # does not ask
perm = await Notifications.request_permissions_async()      # asks if needed
perm = await Notifications.request_permissions_async({"ios": {"allow_provisional": True}})
perm["status"], perm["granted"], perm["canAskAgain"]
```

| `ios` option | Notes |
| --- | --- |
| `allow_alert`, `allow_badge`, `allow_sound` | Default `True`. |
| `allow_provisional` | Quiet notifications without asking. Then `granted` is `False` and `ios.status == IosAuthorizationStatus.PROVISIONAL`. |
| `allow_critical_alerts`, `provide_app_notification_settings`, `allow_display_in_car_play` | Default `False`. |

The response also has `ios` (`status`, `allowsAlert`, `allowsBadge`, `allowsSound`, ...) or `android` (`importance`, `interruptionFilter`). On
Android 13+ the system prompt appears; older Android only reports the setting. In a component, `use_permissions(get=True, request=False)`
returns `(response, request, get)`.

## Content

| Key | Platform | Notes |
| --- | --- | --- |
| `title`, `subtitle`, `body` | both | Strings. |
| `data` | both | A JSON-compatible dict, returned to you on tap. |
| `badge` | iOS | Integer from 0. |
| `sound` | both | `True`, `"default"`, or a file name. |
| `sticky`, `auto_dismiss` | Android | Booleans. |
| `priority` | Android | An `AndroidNotificationPriority`: `MIN` ... `MAX`. |
| `color` | Android | `"#RRGGBB"` or `"#RRGGBBAA"`. |
| `interruption_level` | iOS | `"passive"`, `"active"`, `"timeSensitive"`, `"critical"`. |

`snake_case` and camelCase keys both work. An unknown key raises `ValueError`. `categoryIdentifier`, `attachments`, `launchImageName`,
`vibrate` and `targetContentIdentifier` are accepted but ignored.

## Triggers

Pass `None` (or leave out `trigger`) to show it now.

| Type | Fields | Notes |
| --- | --- | --- |
| `TIME_INTERVAL` | `seconds` (> 0), `repeats` | iOS repeats only every 60 seconds or more. |
| `DATE` | `date`: a `datetime` or milliseconds | Must be in the future. A bare `datetime` or number works as the trigger. |
| `DAILY` | `hour`, `minute` | |
| `WEEKLY` | `weekday` (1 is Sunday), `hour`, `minute` | |
| `MONTHLY` | `day`, `hour`, `minute` | |
| `YEARLY` | `month` (0 is January), `day`, `hour`, `minute` | |
| `CALENDAR` | `year`, `month` (1 is January), `day`, `weekday`, `hour`, `minute`, `second`, `repeats`, `timezone` | iOS only. |

Add `channel_id` to a trigger to post on that Android channel.

```python
from datetime import datetime, timedelta
T = Notifications.SchedulableTriggerInputTypes

await Notifications.schedule_notification_async({"content": {"title": "Wake up"},
                                                 "trigger": {"type": T.DAILY, "hour": 7, "minute": 30}})
await Notifications.schedule_notification_async({"content": {"title": "Soon"},
                                                 "trigger": datetime.now() + timedelta(minutes=10)})
when = await Notifications.get_next_trigger_date_async({"type": T.TIME_INTERVAL, "seconds": 60})
```

## Manage

```python
await Notifications.get_all_scheduled_notifications_async()     # [{"identifier", "content", "trigger"}]
await Notifications.cancel_scheduled_notification_async(nid)
await Notifications.cancel_all_scheduled_notifications_async()
await Notifications.get_presented_notifications_async()         # still shown by the system
await Notifications.dismiss_notification_async(nid)
await Notifications.dismiss_all_notifications_async()
await Notifications.set_badge_count_async(3)                    # iOS. Returns False on Android
await Notifications.get_badge_count_async()                     # always 0 on Android
```

Give `schedule_notification_async` an `"identifier"` to control the id, for example to replace a reminder.

## Foreground handler

```python
Notifications.set_notification_handler({
    "handle_notification": handle,       # may be async. Has 3 seconds to answer
    "handle_success": lambda ident: None,
    "handle_error": lambda ident, err: None,
})
```

`handle_notification(notification)` returns `should_show_banner`, `should_show_list`, `should_play_sound`, `should_set_badge`. A dict or an
object works, with camelCase or snake_case names. On Android the notification posts when banner or list is `True`, and its channel
decides sound and badge. Pass `None` to remove the handler.

## Taps

```python
sub = Notifications.add_notification_response_received_listener(
    lambda r: print(r["notification"]["request"]["content"]["data"], r["actionIdentifier"]))
sub.remove()

last = await Notifications.get_last_notification_response_async()   # includes the tap that launched the app
await Notifications.clear_last_notification_response_async()
```

A plain tap has `actionIdentifier == Notifications.DEFAULT_ACTION_IDENTIFIER`. In a component use `use_last_notification_response()`: it
returns `None` until a tap, then the response. `add_notification_received_listener` is called for each notification that arrives while the
app is open. `Subscription.remove()` is safe to call twice.

## Android channels

```python
await Notifications.set_notification_channel_async("reminders", {
    "name": "Reminders",
    "importance": Notifications.AndroidImportance.HIGH,
    "vibration_pattern": [0, 250, 250, 250],
    "light_color": "#FF8800",
    "lockscreen_visibility": Notifications.AndroidNotificationVisibility.PUBLIC,
})
await Notifications.schedule_notification_async({
    "content": {"title": "Hi"}, "trigger": {"type": "timeInterval", "seconds": 5, "channel_id": "reminders"}})
```

Channel keys: `name`, `importance`, `description`, `sound` (`"default"` or `None`; custom sounds are not supported yet), `vibration_pattern`,
`enable_vibrate`, `enable_lights`, `light_color`, `lockscreen_visibility`, `show_badge`, `bypass_dnd`, `group_id`. Android lets an app
change only the name and description of an existing channel. Also `get_notification_channels_async()`, `get_notification_channel_async(id)`,
`delete_notification_channel_async(id)`. On iOS these return `None`, `[]` or do nothing.

## Remote push

```toml
[tool.pyxpo]
modules = ["notifications"]
plugins = [["pyxpo_notifications.plugin", { googleServicesFile = "./google-services.json" }]]
```

| Plugin option | Notes |
| --- | --- |
| `googleServicesFile` | The `google-services.json` from the Firebase console. Without it Android has no FCM token. |
| `mode` | iOS `aps-environment`: `"development"` (default) or `"production"`. |
| `enableBackgroundRemoteNotifications` | iOS: adds `remote-notification` to the background modes. |

```python
await Notifications.request_permissions_async()
token = await Notifications.get_device_push_token_async()   # {"type": "ios" | "android", "data": "..."}
Notifications.add_push_token_listener(lambda t: print("new token", t))
await Notifications.unregister_for_notifications_async()
```

Send the token to your server and push with APNs or FCM. A received push goes to the same listeners as a local notification, with trigger
`{"type": "push", ...}`. `get_expo_push_token_async()` raises `NotSupportedError`: pyxpo does not use Expo's push service. iOS needs a real
device or a signed simulator build. The `google-services.json` file holds no secrets, but keep the Firebase service account key that
sends pushes out of the app and the repo.

## Errors

| Error | When | What to do |
| --- | --- | --- |
| `ValueError` | A bad trigger (past date, `seconds <= 0`, missing hour), unknown content or request key, bad color, non-JSON `data`. | Fix the argument. The message names it. |
| `NotSupportedError` | Any call on the desktop. A calendar trigger on Android. A custom channel sound. `get_expo_push_token_async()`. | Branch on `platform()`, or use a phone build. |
| `ModuleNotLinkedError` | The phone build lacks the module. | Add `"notifications"` to `modules` and rebuild. |
| `TypeError` | A handler or listener that is not callable. | Pass a function. |
| `granted` is `False` | The user said no. | Explain the value, then send them to Settings if `canAskAgain` is `False`. |

## Config

See [Install](#install) and [Remote push](#remote-push). Nothing else.

## Web and desktop

Not supported. Calls raise `NotSupportedError("notifications need iOS or Android")`, after argument checks. Guard with
`from pyxpo.module_api import platform` if the same code also runs in the desktop window.

## Recipe: daily reminder

```python
from pyxpo import Button, Text, View, component, use_async, use_state
import pyxpo_notifications as Notifications

T = Notifications.SchedulableTriggerInputTypes
REMINDER_ID = "daily-water"


@component
def Water():
    taps, set_taps = use_state(0)
    on, set_on = use_state(False)

    async def run():
        if taps == 0:
            scheduled = await Notifications.get_all_scheduled_notifications_async()
            set_on(any(s["identifier"] == REMINDER_ID for s in scheduled))
            return
        if on:
            await Notifications.cancel_scheduled_notification_async(REMINDER_ID)
            set_on(False)
        else:
            perm = await Notifications.request_permissions_async()
            if perm["granted"]:
                await Notifications.schedule_notification_async({
                    "identifier": REMINDER_ID,
                    "content": {"title": "Drink water", "body": "A glass now"},
                    "trigger": {"type": T.DAILY, "hour": 10, "minute": 0},
                })
                set_on(True)

    use_async(run, [taps])
    return View(Text("Reminder is on" if on else "Reminder is off"),
                Button("Turn off" if on else "Turn on at 10:00", lambda: set_taps(taps + 1)))
```

## Production checklist

- [ ] You ask for permission at a moment the user understands, not at launch.
- [ ] You handle `granted == False` and `canAskAgain == False`.
- [ ] A handler is set if notifications should show while the app is open.
- [ ] Android: reminders use a channel with a suitable importance. Exact alarms are justified if you add `SCHEDULE_EXACT_ALARM`.
- [ ] Tap handling reads `data` and works for the tap that launched the app (`get_last_notification_response_async`).
- [ ] Push: `google-services.json` and `mode="production"` are set for release builds, and the service account key is not in the repo.
- [ ] Tested on a real Android 13+ device and on iOS.

## Testing

Argument checks run on the desktop. Fake the platform and the native call to test the rest:

```python
import asyncio
from datetime import datetime, timedelta

import pytest

import pyxpo_notifications as N


def test_rejects_past_date():
    with pytest.raises(ValueError):
        asyncio.run(N.schedule_notification_async({"content": {"title": "x"}, "trigger": datetime.now() - timedelta(hours=1)}))


def test_rejects_unknown_content_key():
    with pytest.raises(ValueError):
        asyncio.run(N.schedule_notification_async({"content": {"titel": "x"}}))


def test_schedule_sends_clean_request(monkeypatch):
    sent = {}

    async def fake_call(module, method, *args, **kwargs):
        sent["args"] = args
        return "id-1"

    monkeypatch.setattr(N, "platform", lambda: "android")
    monkeypatch.setattr(N, "require_linked", lambda *a, **k: None)
    monkeypatch.setattr(N, "call", fake_call)
    nid = asyncio.run(N.schedule_notification_async({
        "identifier": "a", "content": {"title": "Hi", "color": "#FF8800"},
        "trigger": {"type": N.SchedulableTriggerInputTypes.DAILY, "hour": 7, "minute": 30}}))
    assert nid == "id-1"
    req = sent["args"][0]
    assert req["trigger"] == {"type": "daily", "hour": 7, "minute": 30}
    assert req["content"]["color"] == "#FF8800"


def test_desktop_is_not_supported():
    from pyxpo.module_api import NotSupportedError

    with pytest.raises(NotSupportedError):
        asyncio.run(N.get_permissions_async())
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Nothing shows while the app is open | Set a handler with `set_notification_handler`. |
| Nothing shows on Android 13+ | Permission was not granted. Call `request_permissions_async()`. |
| Fires late on Android | Inexact alarms are batched. Add `SCHEDULE_EXACT_ALARM` if you need exact timing. |
| iOS `ValueError` about repeats | iOS repeats a time interval only every 60 seconds or more. |
| `ValueError: date trigger is in the past` | Use a future time. |
| No FCM token on Android | Add the plugin with `googleServicesFile`, and make sure the Firebase app matches your package name. |
| No push token on the iOS Simulator | Use a real device or a signed simulator build. |
| Android channel changes ignored | Android only lets you change an existing channel's name and description. Use a new channel id. |

## FAQ

**Do I need Firebase?** Only for remote push on Android. Local notifications need nothing.

**Can I attach images or add buttons?** Not yet: categories, action buttons, attachments and custom sounds are not supported.

**Where do I get the Expo push token?** pyxpo does not use Expo's service. Use `get_device_push_token_async()` and push through APNs or FCM.

**Does a scheduled notification survive a restart?** The package registers for `RECEIVE_BOOT_COMPLETED` on Android so schedules can be restored. Test on your target devices.

**Does it work in the desktop window?** No.

## API reference

| Function | Returns |
| --- | --- |
| `get_permissions_async()` / `request_permissions_async(permissions=None)` | Permission dict. |
| `use_permissions(get=True, request=False)` | `(response, request, get)` |
| `schedule_notification_async(request)` | The identifier. |
| `get_next_trigger_date_async(trigger)` | Milliseconds since 1970, or `None`. |
| `get_all_scheduled_notifications_async()` | List of requests. |
| `cancel_scheduled_notification_async(id)` / `cancel_all_scheduled_notifications_async()` | `None` |
| `get_presented_notifications_async()` | List of shown notifications. |
| `dismiss_notification_async(id)` / `dismiss_all_notifications_async()` | `None` |
| `get_badge_count_async()` / `set_badge_count_async(n)` | `int` / `bool` (iOS) |
| `set_notification_handler(handler)` | `None` |
| `add_notification_received_listener(fn)` | `Subscription` |
| `add_notification_response_received_listener(fn)` | `Subscription` |
| `add_push_token_listener(fn)` | `Subscription` |
| `get_last_notification_response_async()` / `clear_last_notification_response_async()` | Response or `None` / `None` |
| `use_last_notification_response()` | Response or `None` |
| `set_notification_channel_async(id, channel)` | Channel dict (Android). |
| `get_notification_channels_async()` / `get_notification_channel_async(id)` / `delete_notification_channel_async(id)` | Android. |
| `get_device_push_token_async()` | `{"type", "data"}` |
| `unregister_for_notifications_async()` | `None` |
| `get_expo_push_token_async()` | Raises `NotSupportedError`. |

Enums: `SchedulableTriggerInputTypes`, `AndroidImportance`, `AndroidNotificationVisibility`, `AndroidNotificationPriority`,
`IosAuthorizationStatus`, `PermissionStatus`. Constant: `DEFAULT_ACTION_IDENTIFIER`. Every function also has its Expo camelCase name.

## License

MIT
