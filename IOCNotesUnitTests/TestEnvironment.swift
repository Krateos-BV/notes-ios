// SPDX-FileCopyrightText: 2026 Krateos B.V.
// SPDX-License-Identifier: GPL-3.0-or-later

import Testing

///
/// Where the unit tests run.
///
/// The unit tests run inside the app as test host, so on a physical device they use the app's real
/// keychain, settings and notes database. Suites that change those must only run on a simulator (XEN-369:
/// a device run cleared the notes cache on a user's phone and left stale sync validators behind).
///
enum TestEnvironment {
    static let isSimulator: Bool = {
        #if targetEnvironment(simulator)
        return true
        #else
        return false
        #endif
    }()

    static let simulatorOnlyReason: Comment = "Changes the app's real account, settings or notes database: simulator only"
}
