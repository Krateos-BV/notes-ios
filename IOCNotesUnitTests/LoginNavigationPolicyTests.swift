// SPDX-FileCopyrightText: 2026 Krateos B.V.
// SPDX-License-Identifier: GPL-3.0-or-later

import Foundation
@testable import iOCNotes
import Testing

///
/// The login web view must stay on the sign-in pages (App Review Guideline 3.1.1, XEN-369).
///
@Suite("Login Navigation Policy Tests")
struct LoginNavigationPolicyTests {
    private func url(_ string: String) throws -> URL {
        try #require(URL(string: string))
    }

    @Test func allowsTheLoginServer() throws {
        let login = try url(NCBrandOptions.shared.loginBaseUrl + "/index.php/login/v2/flow/token")
        #expect(NCBrandOptions.shared.loginAllows(login))
    }

    @Test(arguments: [
        "https://auth.xeniacloud.eu/application/o/authorize/?client_id=x",
        "https://auth.xeniacloud.eu/if/flow/default-authentication-flow/"
    ]) func allowsSignInPages(_ string: String) throws {
        let target = try url(string)
        #expect(NCBrandOptions.shared.loginAllows(target))
    }

    @Test(arguments: [
        "https://auth.xeniacloud.eu/if/flow/xenia-enrollment/",
        "https://xeniacloud.eu/",
        "https://xeniacloud.eu/pricing-en/",
        "https://xeniacloud.eu/privacy-statement-eu/",
        "http://portal.xeniacloud.eu/login"
    ]) func blocksEverythingElse(_ string: String) throws {
        let target = try url(string)
        #expect(NCBrandOptions.shared.loginAllows(target) == false)
    }
}
