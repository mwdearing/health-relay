import XCTest
@testable import HealthBridgeCompanionCore

final class HealthBridgeAppIdentityTests: XCTestCase {
    func testFallbackBundleIdentifierIsPublicNeutral() {
        XCTAssertEqual(
            HealthBridgeAppIdentity.fallbackBundleIdentifier,
            "com.example.HealthBridgeCompanion"
        )
    }

    func testNormalizedBundleIdentifierFallsBackForMissingValues() {
        XCTAssertEqual(
            HealthBridgeAppIdentity.normalizedBundleIdentifier(nil),
            HealthBridgeAppIdentity.fallbackBundleIdentifier
        )
        XCTAssertEqual(
            HealthBridgeAppIdentity.normalizedBundleIdentifier("   "),
            HealthBridgeAppIdentity.fallbackBundleIdentifier
        )
    }

    func testDerivedIdentifiersUseCurrentBundleNamespace() {
        let bundleIdentifier = HealthBridgeAppIdentity.bundleIdentifier

        XCTAssertFalse(bundleIdentifier.isEmpty)
        XCTAssertEqual(
            HealthBridgeAppIdentity.appRefreshIdentifier,
            "\(bundleIdentifier).refresh"
        )
        XCTAssertEqual(
            HealthBridgeAppIdentity.backgroundUploadSessionIdentifier,
            "\(bundleIdentifier).background-upload.v2"
        )
        XCTAssertEqual(
            HealthBridgeAppIdentity.legacyBackgroundUploadSessionIdentifiers,
            ["\(bundleIdentifier).background-upload"]
        )
        XCTAssertFalse(
            HealthBridgeAppIdentity.legacyBackgroundUploadSessionIdentifiers.contains(
                HealthBridgeAppIdentity.backgroundUploadSessionIdentifier
            )
        )
        XCTAssertEqual(
            HealthBridgeAppIdentity.keychainServiceName,
            "\(bundleIdentifier).receiver"
        )
    }

    func testAppRefreshIdentifierPrefersInfoPlistPermittedIdentifier() {
        XCTAssertEqual(
            HealthBridgeAppIdentity.appRefreshIdentifier(
                permittedIdentifiers: ["com.example.BuiltAs.refresh"],
                bundleIdentifier: "team.example.Custom"
            ),
            "com.example.BuiltAs.refresh"
        )
    }

    func testAppRefreshIdentifierSkipsBlankPermittedIdentifiers() {
        XCTAssertEqual(
            HealthBridgeAppIdentity.appRefreshIdentifier(
                permittedIdentifiers: ["  ", "", " a.b.refresh "],
                bundleIdentifier: "team.example.Custom"
            ),
            "a.b.refresh"
        )
    }

    func testAppRefreshIdentifierFallsBackToBundleIdentifier() {
        for permitted in [nil, [], ["", "   "]] as [[String]?] {
            XCTAssertEqual(
                HealthBridgeAppIdentity.appRefreshIdentifier(
                    permittedIdentifiers: permitted,
                    bundleIdentifier: "team.example.Custom"
                ),
                "team.example.Custom.refresh"
            )
        }
    }

    func testAppRefreshIdentifierFromBundleMatchesPermittedIdentifiers() {
        let permitted = Bundle.main.object(
            forInfoDictionaryKey: HealthBridgeAppIdentity.permittedTaskIdentifiersInfoKey
        ) as? [String]
        XCTAssertEqual(
            HealthBridgeAppIdentity.appRefreshIdentifier,
            HealthBridgeAppIdentity.appRefreshIdentifier(
                permittedIdentifiers: permitted,
                bundleIdentifier: HealthBridgeAppIdentity.bundleIdentifier
            )
        )
    }
}
