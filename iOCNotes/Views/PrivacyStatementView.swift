// SPDX-FileCopyrightText: 2026 Krateos B.V.
// SPDX-License-Identifier: GPL-3.0-or-later

import SwiftUI
import WebKit

///
/// Shows the privacy statement in a web view that cannot leave that one page.
///
/// The privacy statement is a page of the public website, whose header and footer link to plans and sign-up.
/// App Review (Guideline 3.1.1) treats such links as a call to action for purchase, so every navigation
/// away from the statement is blocked. Anchors within the page keep working.
///
struct PrivacyStatementView: UIViewRepresentable {
    let url: URL

    func makeCoordinator() -> Coordinator {
        Coordinator(policy: SinglePageNavigationPolicy(url: url))
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsLinkPreview = false
        webView.load(URLRequest(url: url))

        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        // Loaded once in makeUIView. Reloading here would reset the scroll position on every SwiftUI update.
    }

    ///
    /// Enforces ``SinglePageNavigationPolicy`` on the web view.
    ///
    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        let policy: SinglePageNavigationPolicy

        init(policy: SinglePageNavigationPolicy) {
            self.policy = policy
        }

        @objc(webView:decidePolicyForNavigationAction:decisionHandler:)
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
            let allowed = policy.allows(navigationAction.request.url, isMainFrame: navigationAction.targetFrame?.isMainFrame)
            decisionHandler(allowed ? .allow : .cancel)
        }
    }
}

///
/// Allows only the page at the given URL: HTTPS, same host, same path. Query and fragment are ignored.
///
struct SinglePageNavigationPolicy: Equatable {
    let host: String
    let path: String

    init(url: URL) {
        host = (url.host ?? "").lowercased()
        path = Self.normalized(path: url.path)
    }

    ///
    /// - Parameters:
    ///     - url: The navigation target.
    ///     - isMainFrame: `true` for the main frame, `false` for a subframe, `nil` when the target is a new window.
    ///
    func allows(_ url: URL?, isMainFrame: Bool?) -> Bool {
        switch isMainFrame {
        case .none:
            // New windows (target="_blank", window.open) are never opened.
            return false
        case .some(false):
            // Embedded frames (such as a consent widget) may load; anything they open in the main frame is checked again.
            return true
        case .some(true):
            guard let url, url.scheme?.lowercased() == "https", let urlHost = url.host?.lowercased() else {
                return false
            }

            return urlHost == host && Self.normalized(path: url.path) == path
        }
    }

    private static func normalized(path: String) -> String {
        var path = path

        while path.count > 1, path.hasSuffix("/") {
            path.removeLast()
        }

        return path.isEmpty ? "/" : path
    }
}
