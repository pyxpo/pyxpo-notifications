"""Config plugin for remote push, like expo-notifications' config plugin.

    [tool.pyxpo]
    plugins = [["pyxpo_notifications.plugin", {"googleServicesFile": "./google-services.json"}]]

Options:
- `googleServicesFile`: the `google-services.json` from the Firebase console (path relative to the project). Android
  gets the Firebase settings from it, the same values the Google services Gradle plugin would write. Without it
  Android has no FCM token.
- `mode`: iOS `aps-environment` entitlement, "development" (default) or "production".
- `enableBackgroundRemoteNotifications`: iOS, adds `remote-notification` to `UIBackgroundModes`.

The json file has no secrets, but keep the Firebase service account key (used to send pushes) out of the app.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from pyxpo.app_config import ConfigError
from pyxpo.config_plugins import PluginConfig, with_android_res_values, with_entitlements, with_info_plist

MODES = ("development", "production")


def google_services_values(data: Dict[str, Any], package: str) -> Dict[str, Tuple[str, str]]:
    """The Android string resources Firebase reads at startup, for the client whose package is `package`."""
    clients = data.get("client") or []
    found = [((c.get("client_info") or {}).get("android_client_info") or {}).get("package_name") for c in clients]
    client = next((c for c, p in zip(clients, found) if p == package), None)
    if client is None:
        raise ConfigError("google-services.json has no Android app %r (it has: %s). Add the app in the Firebase "
                          "console and download the file again." % (package, ", ".join(p for p in found if p) or "none"))
    project = data.get("project_info") or {}
    out: Dict[str, Tuple[str, str]] = {}

    def put(name: str, value: Any) -> None:
        if value:
            out[name] = ("string", str(value))

    put("google_app_id", (client.get("client_info") or {}).get("mobilesdk_app_id"))
    put("gcm_defaultSenderId", project.get("project_number"))
    put("project_id", project.get("project_id"))
    put("google_storage_bucket", project.get("storage_bucket"))
    put("firebase_database_url", project.get("firebase_url"))
    keys = client.get("api_key") or []
    key = keys[0].get("current_key") if keys else None
    put("google_api_key", key)
    put("google_crash_reporting_api_key", key)
    web = [o for o in client.get("oauth_client") or [] if o.get("client_type") == 3]
    put("default_web_client_id", web[0].get("client_id") if web else None)
    if "google_app_id" not in out:
        raise ConfigError("google-services.json: the client for %r has no mobilesdk_app_id" % package)
    return out


def plugin(config: PluginConfig, props: Dict[str, Any]) -> PluginConfig:
    unknown = set(props) - {"googleServicesFile", "mode", "enableBackgroundRemoteNotifications"}
    if unknown:
        raise ConfigError("pyxpo_notifications.plugin: unknown option(s) %s" % ", ".join(sorted(unknown)))
    mode = props.get("mode", "development")
    if mode not in MODES:
        raise ConfigError("pyxpo_notifications.plugin: mode must be 'development' or 'production', got %r" % (mode,))

    def entitlements(d: Dict[str, Any]) -> None:
        d["aps-environment"] = mode

    with_entitlements(config, entitlements)

    if props.get("enableBackgroundRemoteNotifications"):
        def info_plist(d: Dict[str, Any]) -> None:
            modes = list(d.get("UIBackgroundModes") or [])
            if "remote-notification" not in modes:
                modes.append("remote-notification")
            d["UIBackgroundModes"] = modes

        with_info_plist(config, info_plist)

    path = props.get("googleServicesFile")
    if path:
        f = Path(path) if Path(path).is_absolute() else config.project / path
        package = ((config.native.get("android") or {}).get("package")) or ""

        def res_values(d: Dict[str, Tuple[str, str]]) -> None:
            # Read only in Android builds.
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except OSError as e:
                raise ConfigError("pyxpo_notifications.plugin: cannot read googleServicesFile %s: %s" % (f, e))
            except ValueError as e:
                raise ConfigError("pyxpo_notifications.plugin: %s is not valid JSON: %s" % (f, e))
            d.update(google_services_values(data, package))

        with_android_res_values(config, res_values)
    return config
