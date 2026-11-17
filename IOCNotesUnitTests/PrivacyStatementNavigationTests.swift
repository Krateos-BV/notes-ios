// SPDX-FileCopyrightText: 2026 Krateos B.V.
// SPDX-License-Identifier: GPL-3.0-or-later

import Foundation
@testable import iOCNotes
import Testing

///
/// The privacy statement web view must not reach any other page (App Review Guideline 3.1.1, XEN-369).
///
@Suite("Privacy Statement Navigation Tests")
struct PrivacyStatementNavigationTests {
    private func url(_ string: String) throws -> URL {
        try #require(URL(string: string))
    }

    private func policy() throws -> SinglePageNavigationPolicy {
        SinglePageNavigationPolicy(url: try url(NCBrandOptions.shared.privacyUrl))
    }

    @Test(arguments: [
        "https://xeniacloud.eu/privacy-statement-eu/",
        "https://xeniacloud.eu/privacy-statement-eu",
        "https://XENIACLOUD.eu/privacy-statement-eu/#cookies",
        "https://xeniacloud.eu/privacy-statement-eu/?lang=nl"
    ]) func allowsThePrivacyStatement(_ string: String) throws {
        let target = try url(string)
        #expect(try policy().allows(target, isMainFrame: true))
    }

    @Test(arguments: [
        "https://xeniacloud.eu/",
        "https://xeniacloud.eu/#personal-pricing",
        "https://xeniacloud.eu/#business-pricing",
        "https://xeniacloud.eu/pricing-en/",
        "https://auth.xeniacloud.eu/if/flow/xenia-enrollment/",
        "https://xeniacloud.eu/privacy-policy/",
        "http://xeniacloud.eu/privacy-statement-eu/",
        "https://github.com/Krateos-BV/notes-ios"
    ]) func blocksEveryOtherPage(_ string: String) throws {
        let target = try url(string)
        #expect(try policy().allows(target, isMainFrame: true) == false)
    }

    @Test func blocksNewWindowsEvenForThePrivacyStatement() throws {
        let target = try url(NCBrandOptions.shared.privacyUrl)
        #expect(try policy().allows(target, isMainFrame: nil) == false)
    }

    @Test func allowsSubframes() throws {
        let target = try url("https://challenges.cloudflare.com/widget")
        #expect(try policy().allows(target, isMainFrame: false))
    }

    @Test func blocksMissingURL() throws {
        #expect(try policy().allows(nil, isMainFrame: true) == false)
    }

    @MainActor @Test func coordinatorIsTheNavigationDelegate() throws {
        let coordinator = PrivacyStatementView.Coordinator(policy: try policy())
        #expect(coordinator.responds(to: NSSelectorFromString("webView:decidePolicyForNavigationAction:decisionHandler:")))
    }
}
