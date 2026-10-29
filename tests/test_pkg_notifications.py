"""pyxpo-notifications: expo-notifications API over a fake bridge."""
import asyncio
import datetime as dt
import time

import pytest

from pyxpo.module_api import NotSupportedError, dispatch
from pyxpo.module_testing import FakeBridge

import pyxpo_notifications as N


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean():
    yield
    N._received.clear()
    N._responses.clear()
    N._dropped.clear()
    N._tokens.clear()
    N._handler = None


def bridge(os_name="ios"):
    fb = FakeBridge(os_name)
    for m in ("listen", "setHandler", "presentDecision"):
        fb.on("Notifications", m, lambda *a: None)
    return fb


def note(ident="n1", data=None):
    return {"date": 1.7e12, "request": {"identifier": ident, "content": {"title": "Hi", "data": data or {}},
                                         "trigger": None}}


def test_desktop():
    with pytest.raises(NotSupportedError):
        run(N.get_permissions_async())
    with pytest.raises(NotSupportedError):
        run(N.schedule_notification_async({"content": {"title": "x"}, "trigger": None}))
    assert run(N.get_notification_channels_async()) == []
    sub = N.add_notification_received_listener(lambda n: None)
    sub.remove()
    sub.remove()


def test_not_linked():
    with FakeBridge("ios"):
        with pytest.raises(Exception, match="pyxpo-notifications"):
            run(N.get_permissions_async())


def test_permissions():
    with bridge("ios") as fb:
        fb.on("Notifications", "getPermissions", lambda: {"status": "undetermined", "canAskAgain": True,
                                                          "ios": {"status": 3, "allowsAlert": True}})
        p = run(N.get_permissions_async())
        assert p["status"] == "undetermined" and not p["granted"] and p["expires"] == "never"
        assert p["ios"]["status"] == N.IosAuthorizationStatus.PROVISIONAL
        fb.on("Notifications", "requestPermissions", lambda o: {"status": "granted", "canAskAgain": True, "ios": {}})
        assert run(N.request_permissions_async(ios={"allow_provisional": True, "allowSound": False}))["granted"]
        opts = fb.calls[-1][2][0]
        assert opts["allowProvisional"] and opts["allowAlert"] and opts["allowBadge"] and not opts["allowSound"]
        assert not opts["allowCriticalAlerts"]
        with pytest.raises(ValueError, match="allowNope"):
            run(N.requestPermissionsAsync({"ios": {"allowNope": True}}))


def test_schedule_content_and_triggers():
    with bridge("android") as fb:
        fb.on("Notifications", "schedule", lambda r: r["identifier"])
        ident = run(N.schedule_notification_async({
            "content": {"title": "T", "body": "B", "data": {"a": [1, 2]}, "sound": True, "badge": 3,
                        "auto_dismiss": False, "priority": N.AndroidNotificationPriority.HIGH, "color": "#ff0000"},
            "trigger": {"type": N.SchedulableTriggerInputTypes.TIME_INTERVAL, "seconds": 5, "channel_id": "c"},
            "identifier": "abc"}))
        assert ident == "abc"
        r = fb.calls[-1][2][0]
        assert r["content"] == {"title": "T", "body": "B", "data": {"a": [1, 2]}, "sound": "default", "badge": 3,
                                "autoDismiss": False, "priority": "high", "color": "#ff0000"}
        assert r["trigger"] == {"type": "timeInterval", "seconds": 5, "repeats": False, "channelId": "c"}

        run(N.scheduleNotificationAsync({"content": {"title": "now"}, "trigger": None}))
        r = fb.calls[-1][2][0]
        assert r["trigger"] is None and len(r["identifier"]) == 32 and r["content"]["data"] == {}

        run(N.schedule_notification_async({"content": {}, "trigger": {"channel_id": "c"}}))
        assert fb.calls[-1][2][0]["trigger"] == {"channelId": "c"}
        with pytest.raises(ValueError):
            run(N.get_next_trigger_date_async({"channelId": "c"}))

        later = dt.datetime.now() + dt.timedelta(hours=1)
        run(N.schedule_notification_async({"content": {}, "trigger": later}))
        assert abs(fb.calls[-1][2][0]["trigger"]["date"] - later.timestamp() * 1000) < 1
        run(N.schedule_notification_async({"content": {}, "trigger": {"date": later.timestamp() * 1000}}))
        assert fb.calls[-1][2][0]["trigger"]["type"] == "date"
        run(N.schedule_notification_async({"content": {}, "trigger": {"seconds": 60, "repeats": True}}))
        assert fb.calls[-1][2][0]["trigger"]["type"] == "timeInterval"

        for t, want in (({"type": "daily", "hour": 8, "minute": 30}, {"hour": 8, "minute": 30}),
                        ({"type": "weekly", "weekday": 1, "hour": 0, "minute": 0}, {"weekday": 1}),
                        ({"type": "monthly", "day": 31, "hour": 9, "minute": 0}, {"day": 31}),
                        ({"type": "yearly", "month": 0, "day": 1, "hour": 0, "minute": 0}, {"month": 0})):
            run(N.schedule_notification_async({"content": {}, "trigger": t}))
            got = fb.calls[-1][2][0]["trigger"]
            assert got["type"] == t["type"] and all(got[k] == v for k, v in want.items())

        bad = [{"type": "daily", "hour": 24, "minute": 0}, {"type": "weekly", "weekday": 0, "hour": 1, "minute": 1},
               {"type": "yearly", "month": 12, "day": 1, "hour": 0, "minute": 0}, {"type": "nope"},
               {"type": "timeInterval", "seconds": 0}, {"seconds": 5, "extra": 1}, {"hour": 1},
               dt.datetime.now() - dt.timedelta(seconds=5)]
        for t in bad:
            with pytest.raises(ValueError):
                run(N.schedule_notification_async({"content": {}, "trigger": t}))
        with pytest.raises(NotSupportedError):
            run(N.schedule_notification_async({"content": {}, "trigger": {"type": "calendar", "hour": 1}}))
        for c in ({"data": {"x": object()}}, {"color": "red"}, {"priority": "urgent"}, {"title": 1}, {"nope": 1},
                  {"sound": 3}):
            with pytest.raises(ValueError):
                run(N.schedule_notification_async({"content": c, "trigger": None}))
        with pytest.raises(ValueError):
            run(N.schedule_notification_async({"content": {}, "trigger": None, "extra": 1}))


def test_ios_triggers():
    with bridge("ios") as fb:
        fb.on("Notifications", "schedule", lambda r: r["identifier"])
        run(N.schedule_notification_async({"content": {}, "trigger": {"channelId": "c"}}))
        assert fb.calls[-1][2][0]["trigger"] is None
        with pytest.raises(ValueError, match="60 seconds"):
            run(N.schedule_notification_async({"content": {}, "trigger": {"seconds": 30, "repeats": True}}))
        run(N.schedule_notification_async({"content": {"interruptionLevel": "timeSensitive"},
                                           "trigger": {"type": "calendar", "weekday": 2, "hour": 9,
                                                       "repeats": True, "timezone": "Asia/Kolkata"}}))
        r = fb.calls[-1][2][0]
        assert r["trigger"] == {"type": "calendar", "weekday": 2, "hour": 9, "repeats": True, "timezone": "Asia/Kolkata"}
        assert r["content"]["interruptionLevel"] == "timeSensitive"
        with pytest.raises(ValueError):
            run(N.schedule_notification_async({"content": {}, "trigger": {"type": "calendar"}}))


def test_next_trigger_and_lists():
    with bridge("android") as fb:
        now = time.time() * 1000
        assert run(N.get_next_trigger_date_async({"seconds": 10})) >= now + 10000
        fb.on("Notifications", "nextTriggerDate", lambda t: 123.0)
        assert run(N.get_next_trigger_date_async({"type": "daily", "hour": 1, "minute": 2})) == 123.0
        fb.on("Notifications", "nextTriggerDate", lambda t: None)
        assert run(N.getNextTriggerDateAsync({"type": "daily", "hour": 1, "minute": 2})) is None
        fb.on("Notifications", "getScheduled", lambda: [{"identifier": "a", "content": {}, "trigger": {}}])
        assert run(N.get_all_scheduled_notifications_async())[0]["identifier"] == "a"
        fb.on("Notifications", "getPresented", lambda: [note()])
        assert run(N.get_presented_notifications_async())[0]["request"]["identifier"] == "n1"
        for m in ("cancel", "cancelAll", "dismiss", "dismissAll", "clearLastResponse"):
            fb.on("Notifications", m, lambda *a: None)
        run(N.cancel_scheduled_notification_async("a"))
        run(N.cancel_all_scheduled_notifications_async())
        run(N.dismiss_notification_async("n1"))
        run(N.dismiss_all_notifications_async())
        run(N.clear_last_notification_response_async())
        assert [c[1] for c in fb.calls[-5:]] == ["cancel", "cancelAll", "dismiss", "dismissAll", "clearLastResponse"]
        fb.on("Notifications", "getBadge", lambda: 0)
        fb.on("Notifications", "setBadge", lambda n: False)
        assert run(N.get_badge_count_async()) == 0
        assert run(N.set_badge_count_async(2)) is False
        with pytest.raises(ValueError):
            run(N.set_badge_count_async(-1))


def test_listeners_and_handler():
    with bridge("ios") as fb:
        got, taps = [], []
        sub = N.add_notification_received_listener(got.append)
        N.addNotificationResponseReceivedListener(taps.append)
        assert ("Notifications", "listen", ()) in fb.calls
        dispatch({"name": "Notifications.received", "data": {"notification": note()}})
        assert got == [note()]
        resp = {"notification": note(), "actionIdentifier": N.DEFAULT_ACTION_IDENTIFIER}
        dispatch({"name": "Notifications.response", "data": {"response": resp}})
        assert taps == [resp]
        sub.remove()
        N.remove_notification_subscription(sub)
        dispatch({"name": "Notifications.received", "data": {"notification": note("n2")}})
        assert len(got) == 1

        done = []

        async def handle(n):
            return {"shouldShowBanner": True, "should_play_sound": True}

        N.set_notification_handler({"handle_notification": handle, "handleSuccess": done.append})
        assert fb.calls[-1] == ("Notifications", "setHandler", (True,))

        async def flow():
            dispatch({"name": "Notifications.received", "data": {"notification": note("n3"), "decisionId": 7}})
            for _ in range(5):
                await asyncio.sleep(0)
        run(flow())
        dec = [c for c in fb.calls if c[1] == "presentDecision"][-1]
        assert dec[2] == (7, {"banner": True, "list": False, "sound": True, "badge": False})
        assert done == ["n3"]

        errors = []

        class Bad:
            def handle_notification(self, n):
                raise RuntimeError("boom")

            def handle_error(self, ident, e):
                errors.append((ident, str(e)))

        N.setNotificationHandler(Bad())

        async def flow2():
            dispatch({"name": "Notifications.received", "data": {"notification": note("n4"), "decisionId": 8}})
            for _ in range(5):
                await asyncio.sleep(0)
        run(flow2())
        assert [c for c in fb.calls if c[1] == "presentDecision"][-1][2] == (8, None)
        assert errors == [("n4", "boom")]

        # shouldShowAlert means banner and list.
        assert N._behavior({"should_show_alert": True}) == {"banner": True, "list": True, "sound": False,
                                                            "badge": False}
        N.set_notification_handler(None)
        assert fb.calls[-1] == ("Notifications", "setHandler", (False,))
        with pytest.raises(TypeError):
            N.set_notification_handler({"nope": 1})


def test_last_response():
    with bridge("android") as fb:
        fb.on("Notifications", "getLastResponse", lambda: None)
        assert run(N.get_last_notification_response_async()) is None
        resp = {"notification": note(), "actionIdentifier": N.DEFAULT_ACTION_IDENTIFIER}
        fb.on("Notifications", "getLastResponse", lambda: resp)
        assert run(N.getLastNotificationResponseAsync()) == resp


def test_channels():
    with bridge("ios"):
        assert run(N.set_notification_channel_async("c", {"name": "C"})) is None
        assert run(N.get_notification_channel_async("c")) is None
        assert run(N.get_notification_channels_async()) == []
        run(N.delete_notification_channel_async("c"))
    with bridge("android") as fb:
        fb.on("Notifications", "setChannel", lambda i, c: dict(c, id=i))
        ch = run(N.set_notification_channel_async("alerts", {
            "name": "Alerts", "importance": N.AndroidImportance.HIGH, "vibration_pattern": [0, 250],
            "lockscreen_visibility": N.AndroidNotificationVisibility.SECRET, "light_color": "#00ff00"}))
        assert ch == {"id": "alerts", "name": "Alerts", "importance": 6, "vibrationPattern": [0, 250],
                      "lockscreenVisibility": 3, "lightColor": "#00ff00"}
        assert run(N.setNotificationChannelAsync("d", {"name": "D"}))["importance"] == 5
        for bad in ({"name": ""}, {"name": "x", "importance": 9}, {"name": "x", "nope": 1},
                    {"name": "x", "vibrationPattern": [-1]}, {"name": "x", "lightColor": "green"}):
            with pytest.raises(ValueError):
                run(N.set_notification_channel_async("x", bad))
        with pytest.raises(NotSupportedError):
            run(N.set_notification_channel_async("x", {"name": "x", "sound": "ding.wav"}))
        fb.on("Notifications", "getChannels", lambda: [{"id": "alerts"}])
        fb.on("Notifications", "getChannel", lambda i: None)
        fb.on("Notifications", "deleteChannel", lambda i: None)
        assert run(N.get_notification_channels_async()) == [{"id": "alerts"}]
        assert run(N.get_notification_channel_async("x")) is None
        run(N.delete_notification_channel_async("alerts"))


def test_push_token_and_listener():
    with bridge("android") as fb:
        tok = {"type": "android", "data": "fcm-token"}
        fb.on("Notifications", "getDevicePushToken", lambda: tok)
        fb.on("Notifications", "unregister", lambda: None)
        assert run(N.getDevicePushTokenAsync()) == tok
        got = []
        sub = N.add_push_token_listener(got.append)
        dispatch({"name": "Notifications.token", "data": {"type": "android", "data": "new"}})
        assert got == [{"type": "android", "data": "new"}]
        sub.remove()
        dispatch({"name": "Notifications.token", "data": {"type": "android", "data": "newer"}})
        assert len(got) == 1
        run(N.unregisterForNotificationsAsync())
        assert fb.calls[-1] == ("Notifications", "unregister", ())
        with pytest.raises(NotSupportedError, match="get_device_push_token_async"):
            run(N.get_expo_push_token_async())


def test_push_desktop():
    with pytest.raises(NotSupportedError):
        run(N.get_device_push_token_async())
    with pytest.raises(TypeError):
        N.add_push_token_listener(None)


def test_aliases_exported():
    for name in N.__all__:
        assert hasattr(N, name), name
