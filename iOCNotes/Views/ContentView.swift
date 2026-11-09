// SPDX-FileCopyrightText: Nextcloud GmbH
// SPDX-FileCopyrightText: 2025 Iva Horn
// SPDX-License-Identifier: GPL-3.0-or-later

import NextcloudKitUI
import SwiftUI

///
/// Top level router for views based on availability of local accounts.
///
/// See ``NotesView``, ``SettingsView`` and ``ServerAddressView`` for previews in context of this.
///
struct ContentView: View {
    @Environment(Store.self) var store

    @State var selection: Int = 0

    ///
    /// Show once at launch, after which ``KeychainHelper/shouldShowOnboarding`` is set to false.
    ///
    @State private var isPresentingOnboarding = KeychainHelper.shouldShowOnboarding

    private var onboardingTitle: String {
        let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String ?? NCBrandOptions.shared.brandName

        return String(localized: "Welcome to \(appName)", comment: "Onboarding title. The placeholder is the app name.")
    }

    private var onboardingFeatures: [OnboardingFeature] {
        [
            OnboardingFeature(
                icon: Image(systemName: "lock.shield"),
                title: String(localized: "Your Data, Your Control", comment: "Onboarding feature title."),
                description: String(localized: "Keep your data secure and under your control.", comment: "Onboarding feature description.")
            ),
            OnboardingFeature(
                icon: Image(systemName: "person.2"),
                title: String(localized: "Secure Collaboration", comment: "Onboarding feature title."),
                description: String(localized: "Work together and exchange files securely.", comment: "Onboarding feature description.")
            ),
            OnboardingFeature(
                icon: Image(systemName: "arrow.triangle.2.circlepath"),
                title: String(localized: "Always in Sync", comment: "Onboarding feature title."),
                description: String(localized: "Your notes stay up to date across all your devices.", comment: "Onboarding feature description.")
            ),
            OnboardingFeature(
                icon: Image(systemName: "wifi.slash"),
                title: String(localized: "Works Offline", comment: "Onboarding feature title."),
                description: String(localized: "Keep writing even without a connection.", comment: "Onboarding feature description.")
            )
        ]
    }

    var sharedAccounts: [SharedAccount] {
        store.sharedAccounts.compactMap {
            guard let url = URL(string: $0.url) else {
                return nil
            }

            let image: Image

            if let uiImage = $0.image {
                image = Image(uiImage: uiImage)
            } else {
                image = Image(systemName: "person.circle.fill")
            }

            return SharedAccount($0.user, on: url, with: image)
        }
    }

    var body: some View {
<<<<<<< HEAD
        if store.accounts.isEmpty {
            // `backgroundColor` is painted as an opaque fill inside ServerAddressView's own
            // ZStack, so it's kept transparent here and the real gradient is applied via
            // `.background` below. Its RGB (not alpha) still drives `.readable`'s contrast
            // calculation, so text/icon colors stay correct against the gradient.
            ServerAddressView(backgroundColor: .constant(Color(red: 0 / 255, green: 34 / 255, blue: 102 / 255).opacity(0)), brandImage: Image("BrandLogo"), sharedAccounts: sharedAccounts, userAgent: userAgent, defaultServerAddress: NCBrandOptions.shared.loginBaseUrl, lockServerAddress: NCBrandOptions.shared.disableCustomLoginUrl) { host, name, password in
=======
        if store.accounts.isEmpty && isPresentingOnboarding {
            OnboardingView(title: onboardingTitle, features: onboardingFeatures, tint: Color.accent) {
                withAnimation {
                    isPresentingOnboarding = false
                }
            }
            .onAppear {
                KeychainHelper.shouldShowOnboarding = false
            }
        } else if store.accounts.isEmpty {
            ServerAddressView(backgroundColor: .constant(Color.accent), brandImage: Image("BrandLogo"), sharedAccounts: sharedAccounts, userAgent: userAgent) { host, name, password in
>>>>>>> upstream/main
                store.addAccount(host: host, name: name, password: password)
            }
            .background(
                LinearGradient(
                    colors: [
                        Color(red: 0 / 255, green: 18 / 255, blue: 51 / 255),
                        Color(red: 0 / 255, green: 34 / 255, blue: 102 / 255),
                        Color(red: 0 / 255, green: 51 / 255, blue: 153 / 255)
                    ],
                    startPoint: UnitPoint(x: 0.33, y: 0),
                    endPoint: UnitPoint(x: 0.67, y: 1)
                )
            )
            .ignoresSafeArea()
            .onAppear {
                // The store must update its list of shared accounts when the login user interface is about to appear.
                store.readSharedAccounts()
            }

        } else {
            TabView(selection: $selection) {
                NavigationStack {
                    NotesView()
                }
                .tabItem {
                    Label(
                        title: {
                            Text("Notes")
                        },
                        icon: {
                            Image(systemName: "note")
                        }
                    )
                }
                .tag(0)

                SettingsView()
                .tabItem {
                    Label(
                        title: {
                            Text("Settings")
                        },
                        icon: {
                            Image(systemName: "gear")
                        }
                    )
                }
                .tag(1)
            }
            .tint(Color(NCBrandColor.shared.tintColor))
        }
    }
}
