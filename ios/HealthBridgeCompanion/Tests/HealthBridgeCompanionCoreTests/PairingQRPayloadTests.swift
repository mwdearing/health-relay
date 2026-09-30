import XCTest
@testable import HealthBridgeCompanionCore

final class PairingQRPayloadTests: XCTestCase {
    private let invitationJSON = """
    {
      "schema_id": "health_bridge.receiver_pairing_invitation.v2",
      "schema_version": "2.0.0",
      "label": "test-iphone",
      "receiver_url": "https://health-bridge.example.test/v1/batches",
      "redeem_url": "https://health-bridge.example.test/v1/pairing/redeem",
      "invitation_secret": "hbi_synthetic_secret",
      "expires_at": "2026-07-12T09:00:00Z",
      "transport": "direct"
    }
    """

    private func deepLink(_ json: String, base: String = "healthrelay://pair") -> String {
        let base64 = Data(json.utf8)
            .base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        return "\(base)?payload=\(base64)"
    }

    func testAValidSetupLinkRoutesToTheDeepLinkPath() {
        let scanned = deepLink(invitationJSON)
        guard case let .deepLink(url) = PairingQRPayload.classify(scanned) else {
            return XCTFail("Expected the deep-link route")
        }
        XCTAssertEqual(url.absoluteString, scanned)
    }

    func testSurroundingWhitespaceIsIgnored() {
        guard case .deepLink = PairingQRPayload.classify("  \(deepLink(invitationJSON))\n") else {
            return XCTFail("Expected the deep-link route")
        }
    }

    func testOtherSchemeOrGarbageRoutesToTextSoTheExistingErrorShows() {
        XCTAssertEqual(
            PairingQRPayload.classify("https://example.test/menu"),
            .text("https://example.test/menu")
        )
        XCTAssertEqual(PairingQRPayload.classify(" hello "), .text("hello"))
        XCTAssertEqual(PairingQRPayload.classify(""), .text(""))
    }

    func testUnsupportedSchemaInsideAPairingLinkIsNotAccepted() {
        let bad = invitationJSON.replacingOccurrences(
            of: "health_bridge.receiver_pairing_invitation.v2",
            with: "health_bridge.batch.v1"
        )
        guard case .text = PairingQRPayload.classify(deepLink(bad)) else {
            return XCTFail("An unsupported schema must not route to the deep-link path")
        }
    }
}
