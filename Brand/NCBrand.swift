// SPDX-FileCopyrightText: Nextcloud GmbH
// SPDX-FileCopyrightText: 2017 Marino Faggiana
// SPDX-License-Identifier: GPL-3.0-or-later

import UIKit

let userAgent: String = {
    let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String
    // Original Nextcloud useragent "Mozilla/5.0 (iOS) Nextcloud-iOS/\(appVersion)"
    return "Mozilla/5.0 (iOS) \(appName ?? "")/\(appVersion ?? "")"
}()

class NCBrandOptions: NSObject {
    static let shared: NCBrandOptions = {
        let instance = NCBrandOptions()
        return instance
    }()

    private override init() {}

    var brandName: String = "Xenia Notes"
    var textCopyrightNextcloudiOS: String = "Xenia Notes for iOS %@ © 2026"
    var textCopyrightNextcloudServer: String = "Nextcloud Server %@"
    var loginBaseUrl: String = "https://portal.xeniacloud.eu"

    var privacyUrl: String = "https://xeniacloud.eu/privacy-statement-eu/"
    var sourceCodeUrl: String = "https://github.com/Krateos-BV/notes-ios"

    var capabilitiesGroup: String = "group.eu.xeniacloud.notes"
    var capabilitiesGroupApps: String = "group.eu.xeniacloud.apps"

    var disableCustomLoginUrl: Bool = false
    var disableMultiAccount: Bool = false
}

class NCBrandColor: NSObject {
    static let shared: NCBrandColor = {
        let instance = NCBrandColor()
        return instance
    }()

    let brandColor: UIColor = UIColor(red: 0.0 / 255.0, green: 34.0 / 255.0, blue: 102.0 / 255.0, alpha: 1.0)
    var brandTextColor: UIColor = .white

    // XNT-245: brandColor has no dark-mode variant and is unreadable (~1.15:1
    // contrast) against a dark background when used as the app-wide tint
    // (toolbar icons, Settings, tab bar). tintColor keeps brandColor as-is
    // for light mode but substitutes PHWhiteIcon's own dark-mode grey
    // (#D9D9D9, already proven legible at 14.9:1 and used everywhere else in
    // this app) for dark mode, matching the light/dark provider pattern
    // talk-ios's NCAppBranding.getDynamicColor uses for the same kind of
    // brand color. brandColor itself is left as a fixed value rather than
    // made dynamic -- not because anything else depends on it staying fixed
    // (it doesn't; the login screen's gradient uses its own hardcoded
    // literals, not this property), just to keep "brand color" and
    // "UI tint color" as separate concepts.
    let tintColor: UIColor = UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 0.85, green: 0.85, blue: 0.85, alpha: 1.0)
            : NCBrandColor.shared.brandColor
    }
}
