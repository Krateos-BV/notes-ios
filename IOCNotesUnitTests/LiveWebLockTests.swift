// SPDX-FileCopyrightText: 2026 Krateos B.V.
// SPDX-License-Identifier: GPL-3.0-or-later

import Foundation
@testable import iOCNotes
import Testing
import WebKit

///
/// Loads the live privacy statement and sign-in pages in a `WKWebView` with the app's real navigation delegates,
/// clicks every link to plans, pricing, sign-up or the website, and checks that none of them navigates (XEN-369).
///
/// These tests need network access to xeniacloud.eu and only run when `XENIA_LIVE_WEB_TESTS=1`
/// (pass `TEST_RUNNER_XENIA_LIVE_WEB_TESTS=1` to `xcodebuild test`), so normal CI stays offline and deterministic.
///
@MainActor
@Suite("Live Web Lock Tests", .serialized, .enabled(if: ProcessInfo.processInfo.environment["XENIA_LIVE_WEB_TESTS"] == "1"))
struct LiveWebLockTests {
    /// Every document and shadow root on the page. The sign-in flow (Authentik) renders inside shadow DOM,
    /// so plain `document.querySelectorAll` and `body.innerText` see nothing there.
    private static let deepRoots = """
    (function () {
      const roots = [document];
      for (let i = 0; i < roots.length; i++) {
        roots[i].querySelectorAll('*').forEach(el => { if (el.shadowRoot) roots.push(el.shadowRoot); });
      }
      return roots;
    })()
    """

    /// Links that must never be reachable from inside the app.
    private static let forbiddenLinkScript = """
    \(deepRoots).flatMap(r => Array.from(r.querySelectorAll('a[href]')))
      .map(a => a.href)
      .filter(h => /pricing|enrollment|sign-?up|register|\\/#|^https:\\/\\/xeniacloud\\.eu\\/?$/i.test(h))
    """

    /// Number of form fields rendered anywhere on the page, including shadow DOM.
    private static let inputCountScript = "\(deepRoots).reduce((n, r) => n + r.querySelectorAll('input').length, 0)"

    private func url(_ string: String) throws -> URL {
        try #require(URL(string: string))
    }

    private func makeWebView(delegate: WKNavigationDelegate?) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: 1180, height: 820), configuration: configuration)
        webView.navigationDelegate = delegate
        return webView
    }

    /// Waits until the web view has finished loading, or the timeout passes.
    private func settle(_ webView: WKWebView, timeout: Duration = .seconds(30)) async throws {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout

        try await Task.sleep(for: .milliseconds(500))

        while webView.isLoading, clock.now < deadline {
            try await Task.sleep(for: .milliseconds(250))
        }

        // Give client-side redirects and late navigations a moment.
        try await Task.sleep(for: .seconds(2))
    }

    private func forbiddenLinks(in webView: WKWebView) async throws -> [String] {
        let result = try await webView.evaluateJavaScript(Self.forbiddenLinkScript)
        return Array(Set((result as? [String]) ?? [])).sorted()
    }

    private func click(_ href: String, in webView: WKWebView) async throws {
        let escaped = href.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "'", with: "\\'")
        _ = try await webView.evaluateJavaScript("(\(Self.deepRoots).flatMap(r => Array.from(r.querySelectorAll('a[href]'))).find(a => a.href === '\(escaped)') || {click(){}}).click(); true")
        try await settle(webView, timeout: .seconds(10))
    }

    /// Asserts that every forbidden link on the loaded page stays put, and returns how many were tried.
    private func assertLinksBlocked(in webView: WKWebView, reload: URL) async throws -> Int {
        let start = try #require(webView.url)
        let links = try await forbiddenLinks(in: webView)

        for href in links {
            try await click(href, in: webView)
            let now = webView.url
            #expect(now?.host == start.host && now?.path == start.path, "Link \(href) navigated to \(now?.absoluteString ?? "nil")")

            if now?.path != start.path {
                webView.load(URLRequest(url: reload))
                try await settle(webView)
            }
        }

        return links.count
    }

    // MARK: - Privacy statement

    @Test func privacyStatementLinksDoNotNavigate() async throws {
        let privacy = try url(NCBrandOptions.shared.privacyUrl)
        let coordinator = PrivacyStatementView.Coordinator(policy: SinglePageNavigationPolicy(url: privacy))
        let webView = makeWebView(delegate: coordinator)

        webView.load(URLRequest(url: privacy))
        try await settle(webView)

        #expect(webView.url?.path == privacy.path, "Privacy page did not load: \(webView.url?.absoluteString ?? "nil")")

        let tried = try await assertLinksBlocked(in: webView, reload: privacy)
        #expect(tried > 0, "No pricing or sign-up links found on the live page, so nothing was tested")
        print("[XEN-369] privacy: tried \(tried) forbidden links, final URL \(webView.url?.absoluteString ?? "nil")")
    }

    /// Control: without the lock, the same page really does reach pricing. Proves the test above can fail.
    @Test func controlUnlockedPrivacyStatementNavigates() async throws {
        let privacy = try url(NCBrandOptions.shared.privacyUrl)
        let webView = makeWebView(delegate: nil)

        webView.load(URLRequest(url: privacy))
        try await settle(webView)

        let links = try await forbiddenLinks(in: webView)
        let pricing = try #require(links.first { $0.localizedCaseInsensitiveContains("pricing") && $0.contains("#") == false } ?? links.first)
        try await click(pricing, in: webView)

        #expect(webView.url?.path != privacy.path, "Unlocked web view did not navigate on \(pricing)")
        print("[XEN-369] control: \(pricing) -> \(webView.url?.absoluteString ?? "nil")")
    }

    // MARK: - Login

    @Test func loginPagesLoadAndSignUpIsBlocked() async throws {
        let login = try url(NCBrandOptions.shared.loginBaseUrl + "/login")
        let delegate = NCBrandOptions.shared.makeLoginNavigationDelegate()
        let webView = makeWebView(delegate: delegate)

        webView.load(URLRequest(url: login))
        try await settle(webView)

        let landed = try #require(webView.url)
        print("[XEN-369] login landed on \(landed.absoluteString)")
        #expect(NCBrandOptions.shared.loginAllowedHosts.contains(landed.host ?? ""), "Login ended on a host outside the allow-list: \(landed.absoluteString)")

        // The flow is a client-side app: poll until its form fields render.
        var inputs = 0
        for _ in 0..<40 where inputs == 0 {
            inputs = try await webView.evaluateJavaScript(Self.inputCountScript) as? Int ?? 0
            if inputs == 0 {
                try await Task.sleep(for: .milliseconds(500))
            }
        }

        print("[XEN-369] login: \(inputs) input fields rendered")
        #expect(inputs > 0, "Sign-in form never rendered: the allow-list may block something the sign-in needs")

        let tried = try await assertLinksBlocked(in: webView, reload: login)
        #expect(tried > 0, "No sign-up link found on the sign-in page, so blocking was not exercised")
        print("[XEN-369] login: tried \(tried) forbidden links, final URL \(webView.url?.absoluteString ?? "nil")")
    }
}
