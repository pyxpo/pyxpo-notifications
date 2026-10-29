# Changelog

## Unreleased

- Moved to its own repository, github.com/pyxpo/pyxpo-notifications, with CI on Python 3.9 and 3.12 and releases from tags.
- Added a security policy, a contributing guide and issue templates.

## 0.1.0a2

- Remote push: FCM device token on Android, APNs on iOS, `add_push_token_listener`, `unregister_for_notifications_async`.
- Config plugin `pyxpo_notifications.plugin` (`googleServicesFile`, `mode`, `enableBackgroundRemoteNotifications`).
- Needs `pyxpo>=0.1.0a3` (native contract 3).

## 0.1.0a1

- Docs: detailed README with screenshots, and fuller PyPI metadata (keywords, classifiers, project URLs).
- First release.
