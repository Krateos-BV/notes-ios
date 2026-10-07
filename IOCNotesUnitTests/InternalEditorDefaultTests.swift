// SPDX-FileCopyrightText: 2026 XeniaCloud
// SPDX-License-Identifier: GPL-3.0-or-later

import Foundation
import Testing
@testable import iOCNotes

@Suite("Internal Editor Default Tests", .serialized)
final class InternalEditorDefaultTests {
    private let key = "InternalEditor"
    private let originalValue: Any?

    init() {
        originalValue = UserDefaults.standard.object(forKey: key)
    }

    deinit {
        if let originalValue {
            UserDefaults.standard.set(originalValue, forKey: key)
        } else {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }

    @Test("An Internal Editor setting the user never touched resolves to true")
    func unsetDefaultsToTrue() {
        UserDefaults.standard.removeObject(forKey: key)

        #expect(KeychainHelper.internalEditor)
    }

    @Test("An explicit false is preserved")
    func explicitFalseIsPreserved() {
        UserDefaults.standard.set(false, forKey: key)

        #expect(!KeychainHelper.internalEditor)
    }

    @Test("An explicit true is preserved")
    func explicitTrueIsPreserved() {
        KeychainHelper.internalEditor = true

        #expect(KeychainHelper.internalEditor)
    }
}
