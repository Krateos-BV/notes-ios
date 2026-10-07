// SPDX-FileCopyrightText: 2026 XeniaCloud
// SPDX-License-Identifier: GPL-3.0-or-later

import Testing
@testable import iOCNotes

@Suite("Store Account Tests", .serialized)
final class StoreAccountTests {
    private let originalServer = KeychainHelper.server
    private let originalUsername = KeychainHelper.username
    private let originalPassword = KeychainHelper.password
    private let originalETag = KeychainHelper.eTag
    private let originalLastModified = KeychainHelper.lastModified

    deinit {
        KeychainHelper.server = originalServer
        KeychainHelper.username = originalUsername
        KeychainHelper.password = originalPassword
        KeychainHelper.eTag = originalETag
        KeychainHelper.lastModified = originalLastModified
    }

    ///
    /// XNT-268: a stale `ETag` / `Last-Modified` pair made the first full fetch after logging in again answer `304 Not Modified`, leaving the notes list empty.
    ///
    @Test("Removing the account forgets the sync validators of the previous account")
    func removeAccountResetsSyncValidators() {
        KeychainHelper.server = "https://cloud.example.com"
        KeychainHelper.username = "alice"
        KeychainHelper.password = "secret"
        KeychainHelper.eTag = "\"stale-etag\""
        KeychainHelper.lastModified = 1_790_000_000

        Store().removeAccount()

        #expect(KeychainHelper.eTag.isEmpty)
        #expect(KeychainHelper.lastModified == 0)
        #expect(KeychainHelper.server.isEmpty)
    }
}
