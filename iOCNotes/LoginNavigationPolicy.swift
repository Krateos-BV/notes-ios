// SPDX-FileCopyrightText: 2026 Krateos B.V.
// SPDX-License-Identifier: GPL-3.0-or-later

import Foundation
import NextcloudKitUI
import SwiftUI
import WebKit

extension NCBrandOptions {
    ///
    /// Keeps the login web view on the sign-in pages: only ``loginAllowedHosts`` over HTTPS, never the sign-up (enrollment) flow.
    ///
    /// App Review (Guideline 3.1.1, XEN-369) must not be able to reach plans or pricing from the sign-in screen.
    ///
    var loginNavigationPolicy: WebNavigationPolicy {
        .hosts(loginAllowedHosts, blockingPathsContaining: loginBlockedPathFragments)
    }

    ///
    /// Whether the login web view may show `url`. Exists so unit tests can check the policy without importing NextcloudKitUI.
    ///
    func loginAllows(_ url: URL) -> Bool {
        loginNavigationPolicy.allows(url)
    }
}

#if DEBUG
extension NCBrandOptions {
    ///
    /// The navigation delegate the login sheet uses, so live web tests can drive a `WKWebView` with the real policy enforcement.
    ///
    @MainActor
    func makeLoginNavigationDelegate() -> WKNavigationDelegate {
        WebView(initialURL: .constant(nil), navigationPolicy: loginNavigationPolicy).makeCoordinator()
    }
}
#endif
