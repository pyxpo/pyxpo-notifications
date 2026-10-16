"""Local notifications: the expo-notifications API.

    import pyxpo_notifications as Notifications

    Notifications.set_notification_handler({
        "handle_notification": lambda n: {"should_show_banner": True, "should_show_list": True,
                                          "should_play_sound": False, "should_set_badge": False},
    })
    perm = await Notifications.request_permissions_async()
    await Notifications.schedule_notification_async({
        "content": {"title": "Time's up", "body": "Tea is ready", "data": {"cup": 2}},
        "trigger": {"type": Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, "seconds": 180},
    })
    sub = Notifications.add_notification_response_received_listener(lambda r: print(r["notification"]))

iOS uses UserNotifications. Android posts with `NotificationManager` and schedules with `AlarmManager` (no
Firebase). Remote push tokens need Firebase (Android) or an APNs AppDelegate hook (iOS), which pyxpo does not
have yet: the push token functions raise `NotSupportedError`.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import json
import re
import time
import uuid
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional

from pyxpo.module_api import NotSupportedError, call, on_event, permission_response, platform, require_linked

__version__ = "0.1.0a1"

Notification = Dict[str, Any]
NotificationResponse = Dict[str, Any]
Listener = Callable[[Any], Any]

DEFAULT_ACTION_IDENTIFIER = "expo.modules.notifications.actions.DEFAULT"


class SchedulableTriggerInputTypes(str, Enum):
    CALENDAR = "calendar"          # iOS only: any date components
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    DATE = "date"
    TIME_INTERVAL = "timeInterval"


class AndroidImportance(IntEnum):
    UNKNOWN = 0
    UNSPECIFIED = 1
    NONE = 2
    MIN = 3
    LOW = 4
    DEFAULT = 5
    HIGH = 6
    MAX = 7


class AndroidNotificationVisibility(IntEnum):
    UNKNOWN = 0
    PUBLIC = 1
    PRIVATE = 2
    SECRET = 3


class AndroidNotificationPriority(str, Enum):
    MIN = "min"
    LOW = "low"
    DEFAULT = "default"
    HIGH = "high"
    MAX = "max"


class IosAuthorizationStatus(IntEnum):
    NOT_DETERMINED = 0
    DENIED = 1
    AUTHORIZED = 2
    PROVISIONAL = 3
    EPHEMERAL = 4


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNDETERMINED = "undetermined"


_RECEIVED, _RESPONSE = "Notifications.received", "Notifications.response"


def _native() -> None:
    if platform() == "desktop":
        raise NotSupportedError("notifications need iOS or Android")
    require_linked("Notifications", "notifications", package="pyxpo-notifications")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _keys(d: Optional[Dict[str, Any]], what: str) -> Dict[str, Any]:
    """Expo dicts use camelCase; Python callers may use snake_case."""
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise TypeError("%s must be a dict, got %r" % (what, type(d).__name__))
    return {_camel(str(k)): v for k, v in d.items()}


def _int(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError("%s must be an int from %d to %d, got %r" % (name, lo, hi, value))
    return value


# permissions

_IOS_REQUEST = ("allowAlert", "allowBadge", "allowSound", "allowDisplayInCarPlay", "allowCriticalAlerts",
                "provideAppNotificationSettings", "allowProvisional")


def _permission(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}
    out = permission_response(str(raw.get("status", "denied")), bool(raw.get("canAskAgain", False)))
    for key in ("ios", "android"):
        if isinstance(raw.get(key), dict):
            out[key] = dict(raw[key])
    return out


async def get_permissions_async() -> Dict[str, Any]:
    """The notification permission, without asking. Expo's `NotificationPermissionsStatus`: `status`, `granted`,
    `canAskAgain`, `expires`, plus `ios` (`status` as `IosAuthorizationStatus`, `allowsAlert`, `allowsBadge`,
    `allowsSound`, ...) or `android` (`importance`, `interruptionFilter`). A provisional iOS authorization has
    `granted` False and `ios.status == IosAuthorizationStatus.PROVISIONAL`."""
    _native()
    return _permission(await call("Notifications", "getPermissions", reply=True))


async def request_permissions_async(permissions: Optional[Dict[str, Any]] = None, **kw: Any) -> Dict[str, Any]:
    """Asks to show notifications. iOS options under `ios`: `allow_alert`, `allow_badge`, `allow_sound` (default
    True), `allow_provisional` (quiet notifications without asking), `allow_critical_alerts`,
    `provide_app_notification_settings`, `allow_display_in_car_play`. Android 13+ shows the system prompt (the
    app gets a default channel first, as the prompt needs one); older Android only reports the setting."""
    opts = _keys(permissions, "permissions")
    opts.update(_keys(kw, "options"))
    ios = _keys(opts.get("ios"), "ios")
    unknown = sorted(set(ios) - set(_IOS_REQUEST))
    if unknown:
        raise ValueError("unknown ios permission option(s): %s" % ", ".join(unknown))
    ios = {k: bool(ios.get(k, k in ("allowAlert", "allowBadge", "allowSound"))) for k in _IOS_REQUEST}
    _native()
    return _permission(await call("Notifications", "requestPermissions", ios, reply=True))


def _permission_hook(get: bool, request: bool) -> Any:
    from pyxpo import use_effect, use_state
    status, set_status = use_state(None)

    async def ask() -> Dict[str, Any]:
        res = await request_permissions_async()
        set_status(res)
        return res

    async def read() -> Dict[str, Any]:
        res = await get_permissions_async()
        set_status(res)
        return res

    def start() -> None:
        if request:
            asyncio.ensure_future(ask())
        elif get:
            asyncio.ensure_future(read())

    use_effect(start, [])
    return status, ask, read


def use_permissions(get: bool = True, request: bool = False) -> Any:
    """`(response, request, get)`: the permission (None until read) and coroutines that ask for it or read it
    again and update the component. `get=True` reads it on mount; `request=True` asks on mount."""
    return _permission_hook(get, request)


# content and triggers

_PRIORITIES = {p.value for p in AndroidNotificationPriority}
_LEVELS = ("passive", "active", "timeSensitive", "critical")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
# Expo content keys pyxpo accepts but does not use yet (kept so Expo code ports unchanged).
_IGNORED_CONTENT = ("categoryIdentifier", "launchImageName", "attachments", "vibrate", "targetContentIdentifier")


def _content(content: Any) -> Dict[str, Any]:
    c = _keys(content, "content")
    out: Dict[str, Any] = {}
    for key in ("title", "subtitle", "body"):
        if c.get(key) is not None:
            if not isinstance(c[key], str):
                raise ValueError("content %s must be a string" % key)
            out[key] = c[key]
    data = c.get("data")
    if data is not None:
        if not isinstance(data, dict):
            raise ValueError("content data must be a dict")
        try:
            json.dumps(data)
        except (TypeError, ValueError):
            raise ValueError("content data must be JSON-compatible") from None
        out["data"] = data
    else:
        out["data"] = {}
    if c.get("badge") is not None:
        out["badge"] = _int("content badge", c["badge"], 0, 2 ** 31 - 1)
    sound = c.get("sound")
    if sound is True or sound == "default":
        out["sound"] = "default"
    elif isinstance(sound, str) and sound:
        out["sound"] = sound
    elif sound not in (None, False):
        raise ValueError("content sound must be True, False, 'default' or a sound file name")
    for key in ("sticky", "autoDismiss"):
        if c.get(key) is not None:
            out[key] = bool(c[key])
    if c.get("priority") is not None:
        p = c["priority"].value if isinstance(c["priority"], Enum) else c["priority"]
        if p not in _PRIORITIES:
            raise ValueError("content priority must be an AndroidNotificationPriority, got %r" % (p,))
        out["priority"] = p
    if c.get("color") is not None:
        if not isinstance(c["color"], str) or not _COLOR.match(c["color"]):
            raise ValueError("content color must be '#RRGGBB' or '#RRGGBBAA'")
        out["color"] = c["color"]
    if c.get("interruptionLevel") is not None:
        if c["interruptionLevel"] not in _LEVELS:
            raise ValueError("content interruptionLevel must be one of %s" % ", ".join(_LEVELS))
        out["interruptionLevel"] = c["interruptionLevel"]
    known = {"title", "subtitle", "body", "data", "badge", "sound", "sticky", "autoDismiss", "priority", "color",
             "interruptionLevel"} | set(_IGNORED_CONTENT)
    unknown = sorted(set(c) - known)
    if unknown:
        raise ValueError("unknown content key(s): %s" % ", ".join(unknown))
    return out


def _millis(value: Any) -> float:
    if isinstance(value, _dt.datetime):
        return value.timestamp() * 1000  # a naive datetime is local time
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day).timestamp() * 1000
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("date must be a datetime or milliseconds since 1970, got %r" % (value,))
    return float(value)


_CAL_FIELDS = {"year": (1, 9999), "month": (1, 12), "day": (1, 31), "weekday": (1, 7), "weekOfMonth": (1, 6),
               "weekOfYear": (1, 53), "weekdayOrdinal": (1, 6), "hour": (0, 23), "minute": (0, 59),
               "second": (0, 59)}
_TRIGGER_FIELDS = {
    "timeInterval": ("seconds", "repeats"),
    "date": ("date",),
    "daily": ("hour", "minute"),
    "weekly": ("weekday", "hour", "minute"),
    "monthly": ("day", "hour", "minute"),
    "yearly": ("month", "day", "hour", "minute"),
    "calendar": tuple(_CAL_FIELDS) + ("repeats", "timezone"),
}


def _trigger(trigger: Any) -> Optional[Dict[str, Any]]:
    """Validates an Expo trigger input and returns the dict native code reads (`type` always set)."""
    if trigger is None:
        return None
    if isinstance(trigger, (_dt.date, int, float)) and not isinstance(trigger, bool):
        trigger = {"type": "date", "date": trigger}
    t = _keys(trigger, "trigger")
    kind = t.pop("type", None)
    kind = kind.value if isinstance(kind, Enum) else kind
    if kind is None:
        kind = "timeInterval" if "seconds" in t else "date" if "date" in t else None
        if kind is None and set(t) == {"channelId"}:
            # Expo's ChannelAwareTriggerInput: show now, on that Android channel.
            return {"channelId": str(t["channelId"])} if platform() == "android" else None
        if kind is None:
            raise ValueError("trigger needs a 'type' (a SchedulableTriggerInputTypes value)")
    if kind not in _TRIGGER_FIELDS:
        raise ValueError("unknown trigger type %r" % (kind,))
    channel = t.pop("channelId", None)
    unknown = sorted(set(t) - set(_TRIGGER_FIELDS[kind]))
    if unknown:
        raise ValueError("unknown %s trigger key(s): %s" % (kind, ", ".join(unknown)))
    out: Dict[str, Any] = {"type": kind}
    if kind == "timeInterval":
        s = t.get("seconds")
        if isinstance(s, bool) or not isinstance(s, (int, float)) or s <= 0:
            raise ValueError("timeInterval trigger seconds must be a number > 0")
        out["seconds"] = s
        out["repeats"] = bool(t.get("repeats", False))
        if out["repeats"] and s < 60 and platform() == "ios":
            raise ValueError("iOS repeats a timeInterval trigger only every 60 seconds or more")
    elif kind == "date":
        ms = _millis(t.get("date"))
        if ms <= time.time() * 1000:
            raise ValueError("date trigger is in the past")
        out["date"] = ms
    elif kind == "calendar":
        if platform() == "android":
            raise NotSupportedError("calendar triggers are iOS only; use daily, weekly, monthly or yearly")
        for k, (lo, hi) in _CAL_FIELDS.items():
            if t.get(k) is not None:
                out[k] = _int("calendar trigger %s" % k, t[k], lo, hi)
        if len(out) == 1:
            raise ValueError("calendar trigger needs at least one date component")
        out["repeats"] = bool(t.get("repeats", False))
        if t.get("timezone") is not None:
            out["timezone"] = str(t["timezone"])
    else:
        out["hour"] = _int("%s trigger hour" % kind, t.get("hour"), 0, 23)
        out["minute"] = _int("%s trigger minute" % kind, t.get("minute"), 0, 59)
        if kind == "weekly":
            out["weekday"] = _int("weekly trigger weekday (1 is Sunday)", t.get("weekday"), 1, 7)
        if kind in ("monthly", "yearly"):
            out["day"] = _int("%s trigger day" % kind, t.get("day"), 1, 31)
        if kind == "yearly":
            out["month"] = _int("yearly trigger month (0 is January)", t.get("month"), 0, 11)
    if channel is not None:
        out["channelId"] = str(channel)
    return out


# scheduling

async def schedule_notification_async(request: Dict[str, Any]) -> str:
    """Shows a notification now (`trigger` None) or later; returns its identifier.

    `request`: `{"content": {...}, "trigger": ..., "identifier": optional}`. Content: `title`, `subtitle`,
    `body`, `data` (a JSON-compatible dict), `badge`, `sound` (True for the default sound), and on Android
    `sticky`, `auto_dismiss`, `priority`, `color`, on iOS `interruption_level`. Android shows it on
    `trigger["channel_id"]` (or `content`'s), else on a default channel.

    Triggers (`type` from `SchedulableTriggerInputTypes`): `TIME_INTERVAL` (`seconds`, `repeats`), `DATE`
    (`date`: datetime or milliseconds; a bare datetime or number works too), `DAILY` (`hour`, `minute`),
    `WEEKLY` (`weekday` 1 = Sunday, `hour`, `minute`), `MONTHLY` (`day`, `hour`, `minute`), `YEARLY` (`month`
    0 = January, `day`, `hour`, `minute`), and on iOS `CALENDAR` (date components, `month` 1 = January)."""
    r = _keys(request, "request")
    unknown = sorted(set(r) - {"content", "trigger", "identifier"})
    if unknown:
        raise ValueError("unknown request key(s): %s" % ", ".join(unknown))
    content = _content(r.get("content"))
    trigger = _trigger(r.get("trigger"))
    ident = r.get("identifier") or uuid.uuid4().hex
    if not isinstance(ident, str):
        raise ValueError("identifier must be a string")
    _native()
    return str(await call("Notifications", "schedule", {"identifier": ident, "content": content,
                                                         "trigger": trigger}, reply=True))


async def get_next_trigger_date_async(trigger: Any) -> Optional[float]:
    """When `trigger` would next fire, in milliseconds since 1970, or None if never."""
    t = _trigger(trigger)
    if t is None or "type" not in t:
        raise ValueError("trigger must have a time")
    if t["type"] == "date":
        return t["date"]
    if t["type"] == "timeInterval":
        return time.time() * 1000 + t["seconds"] * 1000
    _native()
    value = await call("Notifications", "nextTriggerDate", t, reply=True)
    return None if value is None else float(value)


async def get_all_scheduled_notifications_async() -> List[Dict[str, Any]]:
    """Requests still waiting to fire: `[{"identifier", "content", "trigger"}]`."""
    _native()
    return [dict(x) for x in await call("Notifications", "getScheduled", reply=True) or []]


async def cancel_scheduled_notification_async(identifier: str) -> None:
    _native()
    await call("Notifications", "cancel", str(identifier), reply=True)


async def cancel_all_scheduled_notifications_async() -> None:
    _native()
    await call("Notifications", "cancelAll", reply=True)


# shown notifications and badge

async def get_presented_notifications_async() -> List[Notification]:
    """Notifications of this app still shown by the system: `[{"date", "request": {"identifier", "content",
    "trigger"}}]`."""
    _native()
    return [dict(x) for x in await call("Notifications", "getPresented", reply=True) or []]


async def dismiss_notification_async(identifier: str) -> None:
    _native()
    await call("Notifications", "dismiss", str(identifier), reply=True)


async def dismiss_all_notifications_async() -> None:
    _native()
    await call("Notifications", "dismissAll", reply=True)


async def get_badge_count_async() -> int:
    """The app icon badge on iOS. Always 0 on Android, where launchers count notifications themselves."""
    _native()
    return int(await call("Notifications", "getBadge", reply=True) or 0)


async def set_badge_count_async(count: int) -> bool:
    """Sets the iOS app icon badge (0 clears it); returns whether it worked. False on Android."""
    count = _int("count", count, 0, 2 ** 31 - 1)
    _native()
    return bool(await call("Notifications", "setBadge", count, reply=True))


# handler and listeners

class Subscription:
    def __init__(self, table: List[Listener], fn: Listener) -> None:
        self._table, self._fn = table, fn

    def remove(self) -> None:
        """Stops the calls. Safe to call twice."""
        if self._fn in self._table:
            self._table.remove(self._fn)


EventSubscription = Subscription

_received: List[Listener] = []
_responses: List[Listener] = []
_dropped: List[Listener] = []
_handler: Any = None
_off: List[Callable[[], None]] = []


def _get(obj: Any, name: str) -> Any:
    """A handler field given as a dict key (camel or snake) or an attribute."""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    if isinstance(obj, dict):
        return obj.get(name, obj.get(snake))
    return getattr(obj, snake, getattr(obj, name, None))


def _listen() -> None:
    """Subscribes to native events once and tells the native module to send them."""
    if not _off:
        _off.append(on_event(_RECEIVED, _on_received))
        _off.append(on_event(_RESPONSE, _on_response))
    if platform() != "desktop":
        _native()
        call("Notifications", "listen")


def _run(fn: Listener, value: Any) -> None:
    res = fn(value)
    if inspect.isawaitable(res):
        asyncio.ensure_future(res)


def _on_received(payload: Any) -> None:
    payload = payload or {}
    n = payload.get("notification") or {}
    for fn in list(_received):
        _run(fn, n)
    did = payload.get("decisionId")
    if did is not None:
        asyncio.ensure_future(_decide(did, n))


def _on_response(payload: Any) -> None:
    r = (payload or {}).get("response")
    if r is None:
        return
    for fn in list(_responses):
        _run(fn, r)


def _behavior(value: Any) -> Dict[str, bool]:
    if not isinstance(value, dict):
        raise TypeError("handle_notification must return a dict of should_* flags")
    b = _keys(value, "behavior")
    alert = bool(b.get("shouldShowAlert", False))  # deprecated in Expo: banner and list
    return {"banner": bool(b.get("shouldShowBanner", alert)), "list": bool(b.get("shouldShowList", alert)),
            "sound": bool(b.get("shouldPlaySound", False)), "badge": bool(b.get("shouldSetBadge", False))}


async def _decide(did: Any, n: Notification) -> None:
    h = _handler
    ident = (n.get("request") or {}).get("identifier")
    if h is None:
        call("Notifications", "presentDecision", did, None)
        return
    try:
        res = _get(h, "handleNotification")(n)
        if inspect.isawaitable(res):
            res = await res
        call("Notifications", "presentDecision", did, _behavior(res))
    except Exception as e:  # noqa: BLE001 - the handler's error goes to handle_error, as in Expo
        call("Notifications", "presentDecision", did, None)
        on_error = _get(h, "handleError")
        if on_error is not None:
            _run(lambda x: on_error(ident, x), e)
        return
    on_success = _get(h, "handleSuccess")
    if on_success is not None:
        _run(on_success, ident)


def set_notification_handler(handler: Any) -> None:
    """Decides how notifications show while the app is open. `handler` has `handle_notification(notification)`
    (may be async) returning `{"should_show_banner", "should_show_list", "should_play_sound",
    "should_set_badge"}`, and optional `handle_success(identifier)` and `handle_error(identifier, error)`; a
    dict or an object, camelCase or snake_case. Without a handler, notifications do not show while the app is
    open. The handler has 3 seconds to answer. Android posts the notification when banner or list is True; its
    channel decides sound and badge."""
    global _handler
    if handler is not None and not callable(_get(handler, "handleNotification")):
        raise TypeError("handler needs a callable handle_notification")
    _handler = handler
    _listen()
    if platform() != "desktop":
        call("Notifications", "setHandler", handler is not None)


setNotificationHandler = set_notification_handler


def _add(table: List[Listener], fn: Listener) -> Subscription:
    if not callable(fn):
        raise TypeError("listener must be callable")
    _listen()
    table.append(fn)
    return Subscription(table, fn)


def add_notification_received_listener(listener: Listener) -> Subscription:
    """Calls `listener(notification)` for each notification that arrives while the app is open."""
    return _add(_received, listener)


def add_notification_response_received_listener(listener: Listener) -> Subscription:
    """Calls `listener(response)` when the user taps a notification while the app runs: `{"notification",
    "actionIdentifier"}` (`DEFAULT_ACTION_IDENTIFIER` for a plain tap). A tap that launched the app is in
    `get_last_notification_response_async()`."""
    return _add(_responses, listener)


def add_notifications_dropped_listener(listener: Listener) -> Subscription:
    """For Expo parity. Never called: dropped notifications are a remote push concept."""
    if not callable(listener):
        raise TypeError("listener must be callable")
    _dropped.append(listener)
    return Subscription(_dropped, listener)


def remove_notification_subscription(subscription: Subscription) -> None:
    subscription.remove()


async def get_last_notification_response_async() -> Optional[NotificationResponse]:
    """The last notification tap, including one that launched the app, or None."""
    _native()
    r = await call("Notifications", "getLastResponse", reply=True)
    return dict(r) if r else None


async def clear_last_notification_response_async() -> None:
    _native()
    await call("Notifications", "clearLastResponse", reply=True)


def use_last_notification_response() -> Optional[NotificationResponse]:
    """The last notification tap (None until read, or if none); updates when the user taps another one."""
    from pyxpo import use_effect, use_state
    value, set_value = use_state(None)

    def start() -> Callable[[], None]:
        async def read() -> None:
            r = await get_last_notification_response_async()
            if r is not None:
                set_value(r)
        asyncio.ensure_future(read())
        return add_notification_response_received_listener(set_value).remove

    use_effect(start, [])
    return value


# Android channels

_CHANNEL_KEYS = ("name", "importance", "bypassDnd", "description", "groupId", "lightColor", "lockscreenVisibility",
                 "showBadge", "sound", "vibrationPattern", "enableLights", "enableVibrate")


async def set_notification_channel_async(channel_id: str, channel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Android: creates or updates a channel (Android lets an app change only its name and description once it
    exists). `channel`: `name`, `importance` (`AndroidImportance`), and optional `description`, `sound` ('default'
    or None for silent), `vibration_pattern` (milliseconds), `enable_vibrate`, `enable_lights`, `light_color`,
    `lockscreen_visibility` (`AndroidNotificationVisibility`), `show_badge`, `bypass_dnd`. Returns the channel;
    None on iOS."""
    c = _keys(channel, "channel")
    unknown = sorted(set(c) - set(_CHANNEL_KEYS))
    if unknown:
        raise ValueError("unknown channel key(s): %s" % ", ".join(unknown))
    if not isinstance(channel_id, str) or not channel_id:
        raise ValueError("channel_id must be a non-empty string")
    if not isinstance(c.get("name"), str) or not c["name"]:
        raise ValueError("channel name must be a non-empty string")
    c["importance"] = int(AndroidImportance(c.get("importance", AndroidImportance.DEFAULT)))
    if c.get("lockscreenVisibility") is not None:
        c["lockscreenVisibility"] = int(AndroidNotificationVisibility(c["lockscreenVisibility"]))
    if c.get("lightColor") is not None and not (isinstance(c["lightColor"], str) and _COLOR.match(c["lightColor"])):
        raise ValueError("channel light_color must be '#RRGGBB' or '#RRGGBBAA'")
    vp = c.get("vibrationPattern")
    if vp is not None and (not isinstance(vp, (list, tuple)) or not all(
            isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in vp)):
        raise ValueError("channel vibration_pattern must be a list of milliseconds")
    if c.get("sound") not in (None, "default"):
        raise NotSupportedError("channel sound must be 'default' or None; custom sounds are not supported yet")
    if platform() != "android":
        return None
    _native()
    return dict(await call("Notifications", "setChannel", channel_id, c, reply=True))


async def get_notification_channels_async() -> List[Dict[str, Any]]:
    """Android: the app's channels. Empty on iOS."""
    if platform() != "android":
        return []
    _native()
    return [dict(x) for x in await call("Notifications", "getChannels", reply=True) or []]


async def get_notification_channel_async(channel_id: str) -> Optional[Dict[str, Any]]:
    """Android: one channel, or None. None on iOS."""
    if platform() != "android":
        return None
    _native()
    c = await call("Notifications", "getChannel", str(channel_id), reply=True)
    return dict(c) if c else None


async def delete_notification_channel_async(channel_id: str) -> None:
    """Android: deletes a channel. Does nothing on iOS."""
    if platform() != "android":
        return
    _native()
    await call("Notifications", "deleteChannel", str(channel_id), reply=True)


# remote push (not supported yet)

def _no_push(name: str) -> NotSupportedError:
    return NotSupportedError("%s needs remote push (Firebase on Android, an APNs AppDelegate hook on iOS), which "
                             "pyxpo does not support yet; local notifications work" % name)


async def get_device_push_token_async() -> Dict[str, Any]:
    raise _no_push("get_device_push_token_async")


async def get_expo_push_token_async(options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raise _no_push("get_expo_push_token_async")


def add_push_token_listener(listener: Listener) -> Subscription:
    raise _no_push("add_push_token_listener")


getPermissionsAsync = get_permissions_async
requestPermissionsAsync = request_permissions_async
usePermissions = use_permissions
scheduleNotificationAsync = schedule_notification_async
getNextTriggerDateAsync = get_next_trigger_date_async
getAllScheduledNotificationsAsync = get_all_scheduled_notifications_async
cancelScheduledNotificationAsync = cancel_scheduled_notification_async
cancelAllScheduledNotificationsAsync = cancel_all_scheduled_notifications_async
getPresentedNotificationsAsync = get_presented_notifications_async
dismissNotificationAsync = dismiss_notification_async
dismissAllNotificationsAsync = dismiss_all_notifications_async
getBadgeCountAsync = get_badge_count_async
setBadgeCountAsync = set_badge_count_async
addNotificationReceivedListener = add_notification_received_listener
addNotificationResponseReceivedListener = add_notification_response_received_listener
addNotificationsDroppedListener = add_notifications_dropped_listener
removeNotificationSubscription = remove_notification_subscription
getLastNotificationResponseAsync = get_last_notification_response_async
clearLastNotificationResponseAsync = clear_last_notification_response_async
useLastNotificationResponse = use_last_notification_response
setNotificationChannelAsync = set_notification_channel_async
getNotificationChannelsAsync = get_notification_channels_async
getNotificationChannelAsync = get_notification_channel_async
deleteNotificationChannelAsync = delete_notification_channel_async
getDevicePushTokenAsync = get_device_push_token_async
getExpoPushTokenAsync = get_expo_push_token_async
addPushTokenListener = add_push_token_listener

__all__ = [
    "DEFAULT_ACTION_IDENTIFIER", "SchedulableTriggerInputTypes", "AndroidImportance", "AndroidNotificationVisibility",
    "AndroidNotificationPriority", "IosAuthorizationStatus", "PermissionStatus", "Subscription", "EventSubscription",
    "get_permissions_async", "request_permissions_async", "use_permissions", "schedule_notification_async",
    "get_next_trigger_date_async", "get_all_scheduled_notifications_async", "cancel_scheduled_notification_async",
    "cancel_all_scheduled_notifications_async", "get_presented_notifications_async", "dismiss_notification_async",
    "dismiss_all_notifications_async", "get_badge_count_async", "set_badge_count_async", "set_notification_handler",
    "add_notification_received_listener", "add_notification_response_received_listener",
    "add_notifications_dropped_listener", "remove_notification_subscription", "get_last_notification_response_async",
    "clear_last_notification_response_async", "use_last_notification_response", "set_notification_channel_async",
    "get_notification_channels_async", "get_notification_channel_async", "delete_notification_channel_async",
    "get_device_push_token_async", "get_expo_push_token_async", "add_push_token_listener",
    "getPermissionsAsync", "requestPermissionsAsync", "usePermissions", "scheduleNotificationAsync",
    "getNextTriggerDateAsync", "getAllScheduledNotificationsAsync", "cancelScheduledNotificationAsync",
    "cancelAllScheduledNotificationsAsync", "getPresentedNotificationsAsync", "dismissNotificationAsync",
    "dismissAllNotificationsAsync", "getBadgeCountAsync", "setBadgeCountAsync", "setNotificationHandler",
    "addNotificationReceivedListener", "addNotificationResponseReceivedListener", "addNotificationsDroppedListener",
    "removeNotificationSubscription", "getLastNotificationResponseAsync", "clearLastNotificationResponseAsync",
    "useLastNotificationResponse", "setNotificationChannelAsync", "getNotificationChannelsAsync",
    "getNotificationChannelAsync", "deleteNotificationChannelAsync", "getDevicePushTokenAsync",
    "getExpoPushTokenAsync", "addPushTokenListener",
]
