"""pyxpo_notifications.plugin: google-services.json values, iOS entitlement and background mode."""
import json

import pytest

from pyxpo.app_config import ConfigError
from pyxpo.config_plugins import Mods, PluginConfig

from pyxpo_notifications.plugin import google_services_values, plugin

GS = {
    "project_info": {"project_number": "1234", "project_id": "demo-proj", "storage_bucket": "demo.appspot.com",
                     "firebase_url": "https://demo.firebaseio.com"},
    "client": [
        {"client_info": {"mobilesdk_app_id": "1:1234:android:other",
                         "android_client_info": {"package_name": "com.other"}},
         "api_key": [{"current_key": "k0"}]},
        {"client_info": {"mobilesdk_app_id": "1:1234:android:abc",
                         "android_client_info": {"package_name": "com.example.app"}},
         "oauth_client": [{"client_id": "a.apps", "client_type": 1}, {"client_id": "web.apps", "client_type": 3}],
         "api_key": [{"current_key": "key1"}]},
    ],
}


def config(tmp_path, package="com.example.app"):
    return PluginConfig({"android": {"package": package}}, tmp_path, Mods())


def test_values_for_package():
    v = google_services_values(GS, "com.example.app")
    assert v["google_app_id"] == ("string", "1:1234:android:abc")
    assert v["gcm_defaultSenderId"] == ("string", "1234")
    assert v["project_id"] == ("string", "demo-proj")
    assert v["google_storage_bucket"] == ("string", "demo.appspot.com")
    assert v["firebase_database_url"] == ("string", "https://demo.firebaseio.com")
    assert v["google_api_key"] == v["google_crash_reporting_api_key"] == ("string", "key1")
    assert v["default_web_client_id"] == ("string", "web.apps")


def test_values_missing_package():
    with pytest.raises(ConfigError, match="com.other, com.example.app"):
        google_services_values(GS, "com.nope")


def test_plugin_android_and_ios(tmp_path):
    (tmp_path / "google-services.json").write_text(json.dumps(GS))
    c = config(tmp_path)
    plugin(c, {"googleServicesFile": "./google-services.json", "mode": "production",
               "enableBackgroundRemoteNotifications": True})
    res = c.mods.apply_res_values({})
    assert res["google_app_id"] == ("string", "1:1234:android:abc")
    assert c.mods.apply_entitlements({}) == {"aps-environment": "production"}
    plist = c.mods.apply_info_plist({"UIBackgroundModes": ["audio"]})
    assert plist["UIBackgroundModes"] == ["audio", "remote-notification"]


def test_plugin_defaults_and_errors(tmp_path):
    c = config(tmp_path)
    plugin(c, {})
    assert c.mods.apply_entitlements({}) == {"aps-environment": "development"}
    assert not c.mods.info_plist and not c.mods.android_res_values
    with pytest.raises(ConfigError, match="mode"):
        plugin(config(tmp_path), {"mode": "prod"})
    with pytest.raises(ConfigError, match="unknown option"):
        plugin(config(tmp_path), {"googleServices": "x"})
    c = config(tmp_path)
    plugin(c, {"googleServicesFile": "missing.json"})
    with pytest.raises(ConfigError, match="cannot read"):
        c.mods.apply_res_values({})
    (tmp_path / "bad.json").write_text("{")
    c = config(tmp_path)
    plugin(c, {"googleServicesFile": "bad.json"})
    with pytest.raises(ConfigError, match="not valid JSON"):
        c.mods.apply_res_values({})
