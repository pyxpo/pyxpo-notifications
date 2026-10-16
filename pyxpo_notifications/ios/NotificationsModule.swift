import UIKit
import UserNotifications

/// expo-notifications on iOS: UserNotifications permissions, scheduling, shown notifications, the badge, and the
/// foreground and tap delegate. The delegate is installed at launch (`PxAppLaunchObserver`) so a tap that launches
/// the app is kept for `getLastResponse`. All state lives on the main thread.
final class NotificationsModule: PxModule, PxAppLaunchObserver {
    static let moduleName = "Notifications"

    static func appDidFinishLaunching(_ options: [AnyHashable: Any]?) {
        NotificationsDelegate.shared.install()
    }

    private var center: UNUserNotificationCenter { UNUserNotificationCenter.current() }

    required init() { NotificationsDelegate.shared.install() }

    func call(_ ctx: PxModuleContext, _ method: String, _ args: [Any], _ reply: @escaping PxReply) {
        let d = NotificationsDelegate.shared
        d.ctx = ctx
        switch method {
        case "listen":
            reply(nil, nil)
        case "setHandler":
            d.handlerSet = args.first as? Bool ?? false
            reply(nil, nil)
        case "presentDecision":
            d.decide((args.first as? NSNumber)?.intValue ?? -1, args.count > 1 ? args[1] as? [String: Any] : nil)
            reply(nil, nil)
        case "getPermissions":
            center.getNotificationSettings { s in reply(Self.permission(s), nil) }
        case "requestPermissions":
            requestPermissions(args.first as? [String: Any] ?? [:], reply)
        case "schedule":
            schedule(args.first as? [String: Any] ?? [:], reply)
        case "nextTriggerDate":
            let t = (try? Self.trigger(args.first as? [String: Any])) ?? nil
            if let c = t as? UNCalendarNotificationTrigger {
                reply(c.nextTriggerDate().map { $0.timeIntervalSince1970 * 1000 } ?? NSNull(), nil)
            } else if let i = t as? UNTimeIntervalNotificationTrigger {
                reply(i.nextTriggerDate().map { $0.timeIntervalSince1970 * 1000 } ?? NSNull(), nil)
            } else {
                reply(NSNull(), nil)
            }
        case "getScheduled":
            center.getPendingNotificationRequests { reqs in reply(reqs.map(Self.request), nil) }
        case "cancel":
            center.removePendingNotificationRequests(withIdentifiers: [args.first as? String ?? ""])
            reply(nil, nil)
        case "cancelAll":
            center.removeAllPendingNotificationRequests()
            reply(nil, nil)
        case "getPresented":
            center.getDeliveredNotifications { ns in reply(ns.map(Self.notification), nil) }
        case "dismiss":
            center.removeDeliveredNotifications(withIdentifiers: [args.first as? String ?? ""])
            reply(nil, nil)
        case "dismissAll":
            center.removeAllDeliveredNotifications()
            reply(nil, nil)
        case "getBadge":
            reply(UIApplication.shared.applicationIconBadgeNumber, nil)
        case "setBadge":
            let n = (args.first as? NSNumber)?.intValue ?? 0
            if #available(iOS 16.0, *) {
                center.setBadgeCount(n) { error in reply(error == nil, nil) }
            } else {
                UIApplication.shared.applicationIconBadgeNumber = n
                reply(true, nil)
            }
        case "getLastResponse":
            reply(d.lastResponse ?? NSNull(), nil)
        case "clearLastResponse":
            d.lastResponse = nil
            reply(nil, nil)
        default:
            reply(nil, "Notifications has no method \(method)")
        }
    }

    private func requestPermissions(_ ios: [String: Any], _ reply: @escaping PxReply) {
        func on(_ key: String) -> Bool { ios[key] as? Bool ?? false }
        var opts: UNAuthorizationOptions = []
        if on("allowAlert") { opts.insert(.alert) }
        if on("allowBadge") { opts.insert(.badge) }
        if on("allowSound") { opts.insert(.sound) }
        if on("allowDisplayInCarPlay") { opts.insert(.carPlay) }
        if on("allowCriticalAlerts") { opts.insert(.criticalAlert) }
        if on("provideAppNotificationSettings") { opts.insert(.providesAppNotificationSettings) }
        if on("allowProvisional") { opts.insert(.provisional) }
        center.requestAuthorization(options: opts) { [center] _, error in
            if let e = error { reply(nil, "Could not ask for notifications: \(e.localizedDescription)"); return }
            center.getNotificationSettings { s in reply(Self.permission(s), nil) }
        }
    }

    private func schedule(_ req: [String: Any], _ reply: @escaping PxReply) {
        let id = req["identifier"] as? String ?? UUID().uuidString
        let c = req["content"] as? [String: Any] ?? [:]
        let trig = req["trigger"] as? [String: Any]
        let content = UNMutableNotificationContent()
        if let v = c["title"] as? String { content.title = v }
        if let v = c["subtitle"] as? String { content.subtitle = v }
        if let v = c["body"] as? String { content.body = v }
        if let v = c["badge"] as? NSNumber { content.badge = v }
        if let s = c["sound"] as? String {
            content.sound = s == "default" ? .default : UNNotificationSound(named: UNNotificationSoundName(s))
        }
        if let level = c["interruptionLevel"] as? String {
            content.interruptionLevel = ["passive": .passive, "timeSensitive": .timeSensitive,
                                         "critical": .critical][level] ?? .active
        }
        var info: [String: Any] = ["pyxpoData": c["data"] as? [String: Any] ?? [:]]
        if let t = trig { info["pyxpoTrigger"] = t }
        content.userInfo = info
        let trigger: UNNotificationTrigger?
        do { trigger = try Self.trigger(trig) } catch { reply(nil, "\(error)"); return }
        center.add(UNNotificationRequest(identifier: id, content: content, trigger: trigger)) { error in
            if let e = error { reply(nil, "Could not schedule the notification: \(e.localizedDescription)"); return }
            reply(id, nil)
        }
    }

    struct TriggerError: Error, CustomStringConvertible { let description: String }

    /// The UNNotificationTrigger for a trigger dict the Python side validated (nil: show now).
    static func trigger(_ t: [String: Any]?) throws -> UNNotificationTrigger? {
        guard let t = t, let type = t["type"] as? String else { return nil }
        func int(_ k: String) -> Int? { (t[k] as? NSNumber)?.intValue }
        var dc = DateComponents()
        switch type {
        case "timeInterval":
            let s = (t["seconds"] as? NSNumber)?.doubleValue ?? 1
            let repeats = t["repeats"] as? Bool ?? false
            if repeats && s < 60 { throw TriggerError(description: "iOS repeats a timeInterval trigger only every 60 seconds or more") }
            return UNTimeIntervalNotificationTrigger(timeInterval: s, repeats: repeats)
        case "date":
            let date = Date(timeIntervalSince1970: ((t["date"] as? NSNumber)?.doubleValue ?? 0) / 1000)
            dc = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
            return UNCalendarNotificationTrigger(dateMatching: dc, repeats: false)
        case "daily", "weekly", "monthly", "yearly":
            dc.hour = int("hour")
            dc.minute = int("minute")
            if type == "weekly" { dc.weekday = int("weekday") }
            if type == "monthly" || type == "yearly" { dc.day = int("day") }
            if type == "yearly" { dc.month = int("month").map { $0 + 1 } }
            return UNCalendarNotificationTrigger(dateMatching: dc, repeats: true)
        case "calendar":
            dc.year = int("year")
            dc.month = int("month")
            dc.day = int("day")
            dc.weekday = int("weekday")
            dc.weekOfMonth = int("weekOfMonth")
            dc.weekOfYear = int("weekOfYear")
            dc.weekdayOrdinal = int("weekdayOrdinal")
            dc.hour = int("hour")
            dc.minute = int("minute")
            dc.second = int("second")
            if let tz = t["timezone"] as? String {
                guard let zone = TimeZone(identifier: tz) else { throw TriggerError(description: "unknown timezone \(tz)") }
                dc.timeZone = zone
            }
            return UNCalendarNotificationTrigger(dateMatching: dc, repeats: t["repeats"] as? Bool ?? false)
        default:
            throw TriggerError(description: "unknown trigger type \(type)")
        }
    }

    // MARK: serialization (Expo shapes)

    static func request(_ r: UNNotificationRequest) -> [String: Any] {
        let info = r.content.userInfo
        var data: Any = info["pyxpoData"] ?? NSNull()
        var trigger: Any = info["pyxpoTrigger"] ?? NSNull()
        if r.trigger is UNPushNotificationTrigger {
            var d: [String: Any] = [:]
            for (k, v) in info { if let k = k as? String, k != "aps" { d[k] = v } }
            data = d
            trigger = ["type": "push"]
        }
        if !JSONSerialization.isValidJSONObject(["d": data]) { data = [String: Any]() }
        var content: [String: Any] = ["title": r.content.title.isEmpty ? NSNull() : r.content.title,
                                      "subtitle": r.content.subtitle.isEmpty ? NSNull() : r.content.subtitle,
                                      "body": r.content.body.isEmpty ? NSNull() : r.content.body,
                                      "data": data, "badge": r.content.badge ?? NSNull(),
                                      "sound": r.content.sound == nil ? NSNull() : "default",
                                      "categoryIdentifier": r.content.categoryIdentifier.isEmpty ? NSNull() : r.content.categoryIdentifier]
        if #available(iOS 15.0, *) {
            content["interruptionLevel"] = [.passive: "passive", .timeSensitive: "timeSensitive",
                                            .critical: "critical"][r.content.interruptionLevel] ?? "active"
        }
        return ["identifier": r.identifier, "content": content, "trigger": trigger]
    }

    static func notification(_ n: UNNotification) -> [String: Any] {
        ["date": n.date.timeIntervalSince1970 * 1000, "request": request(n.request)]
    }

    static func permission(_ s: UNNotificationSettings) -> [String: Any] {
        func flag(_ v: UNNotificationSetting) -> Any {
            switch v {
            case .enabled: return true
            case .disabled: return false
            default: return NSNull()
            }
        }
        let status: String
        switch s.authorizationStatus {
        case .authorized: status = "granted"
        case .denied: status = "denied"
        default: status = "undetermined"  // provisional and ephemeral too, as in Expo
        }
        var ios: [String: Any] = [
            "status": s.authorizationStatus.rawValue,
            "allowsDisplayInNotificationCenter": flag(s.notificationCenterSetting),
            "allowsDisplayOnLockScreen": flag(s.lockScreenSetting),
            "allowsDisplayInCarPlay": flag(s.carPlaySetting),
            "allowsAlert": flag(s.alertSetting),
            "allowsBadge": flag(s.badgeSetting),
            "allowsSound": flag(s.soundSetting),
            "allowsCriticalAlerts": flag(s.criticalAlertSetting),
            "alertStyle": s.alertStyle.rawValue,
            "allowsPreviews": s.showPreviewsSetting.rawValue,
            "providesAppNotificationSettings": s.providesAppNotificationSettings,
            "allowsAnnouncements": flag(s.announcementSetting),
        ]
        if #available(iOS 15.0, *) {
            ios["allowsTimeSensitive"] = flag(s.timeSensitiveSetting)
        }
        return ["status": status, "canAskAgain": s.authorizationStatus != .denied, "ios": ios]
    }
}

/// The UNUserNotificationCenter delegate. One per process, so it outlives Python reloads and module instances.
final class NotificationsDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationsDelegate()
    static let defaultAction = "expo.modules.notifications.actions.DEFAULT"

    weak var ctx: PxModuleContext?
    var handlerSet = false
    var lastResponse: [String: Any]?
    private var installed = false
    private var nextDecision = 1
    private var pending: [Int: (UNNotificationPresentationOptions) -> Void] = [:]

    func install() {
        if installed { return }
        installed = true
        UNUserNotificationCenter.current().delegate = self
    }

    func decide(_ id: Int, _ b: [String: Any]?) {
        guard let done = pending.removeValue(forKey: id) else { return }
        var opts: UNNotificationPresentationOptions = []
        if let b = b {
            if b["banner"] as? Bool == true { opts.insert(.banner) }
            if b["list"] as? Bool == true { opts.insert(.list) }
            if b["sound"] as? Bool == true { opts.insert(.sound) }
            if b["badge"] as? Bool == true { opts.insert(.badge) }
        }
        done(opts)
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        let n = NotificationsModule.notification(notification)
        DispatchQueue.main.async {
            guard let ctx = self.ctx else { done([]); return }
            guard self.handlerSet else {
                ctx.emit("Notifications.received", ["notification": n])
                done([])
                return
            }
            let id = self.nextDecision
            self.nextDecision += 1
            self.pending[id] = done
            ctx.emit("Notifications.received", ["notification": n, "decisionId": id])
            // Expo gives the handler 3 seconds; after that the notification is not shown.
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.decide(id, nil) }
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        let action = response.actionIdentifier == UNNotificationDefaultActionIdentifier
            ? Self.defaultAction : response.actionIdentifier
        let r: [String: Any] = ["notification": NotificationsModule.notification(response.notification),
                                "actionIdentifier": action]
        DispatchQueue.main.async {
            self.lastResponse = r
            self.ctx?.emit("Notifications.response", ["response": r])
            done()
        }
    }
}
