import XCTest
@testable import HealthBridgeCompanionCore

final class InvitationCodeFormatterTests: XCTestCase {
    private let canonical = "ABCDE-FGHJK-MNPQR"
    private let server = "https://health-bridge.example.test"

    func testGroupsAsTheCodeIsTyped() {
        XCTAssertEqual(InvitationCodeFormatter.formatted(""), "")
        XCTAssertEqual(InvitationCodeFormatter.formatted("abcde"), "ABCDE")
        XCTAssertEqual(InvitationCodeFormatter.formatted("abcdef"), "ABCDE-F")
        XCTAssertEqual(InvitationCodeFormatter.formatted("abcdefghijk"), "ABCDE-FGHIJ-K")
        XCTAssertEqual(InvitationCodeFormatter.formatted("abcdefghjkmnpqr"), canonical)
    }

    func testBackspaceOverAGroupSeparatorKeepsTheLetters() {
        XCTAssertEqual(InvitationCodeFormatter.formatted("ABCDE-"), "ABCDE")
    }

    func testLowercasePastedSpacedAndHyphenatedInputAllBecomeCanonical() {
        for input in [
            "abcdefghjkmnpqr",
            "ABCDE-FGHJK-MNPQR",
            " abcde fghjk mnpqr ",
            "abcde-fghjk-mnpqr",
            "ABCDE\nFGHJK\tMNPQR\n",
            "abcdefghjk-mnpqr",
        ] {
            XCTAssertEqual(InvitationCodeFormatter.formatted(input), canonical, input)
        }
    }

    func testFormatterIsIdempotent() {
        let once = InvitationCodeFormatter.formatted("abcde fghjk mn")
        XCTAssertEqual(InvitationCodeFormatter.formatted(once), once)
    }

    func testCompleteCodeIsAcceptedAsSubmitted() throws {
        for input in ["abcdefghjkmnpqr", " abcde fghjk mnpqr ", "abcde-fghjk-mnpqr"] {
            let formatted = InvitationCodeFormatter.formatted(input)
            let manual = try ReceiverManualPairing(serverURLString: server, invitationCode: formatted)
            XCTAssertEqual(manual.invitationCode, formatted)
            XCTAssertEqual(manual.invitationCode, canonical)
        }
    }

    func testCharactersOutsideTheAlphabetAreKeptSoValidationStillRejects() {
        // 0, 1, I and O are not in the code alphabet. They must not be dropped silently.
        let formatted = InvitationCodeFormatter.formatted("abcde fghjk mnpq0")
        XCTAssertEqual(formatted, "ABCDE-FGHJK-MNPQ0")
        XCTAssertThrowsError(try ReceiverManualPairing(serverURLString: server, invitationCode: formatted)) { error in
            XCTAssertEqual(error as? ReceiverPairingBundleError, .invalidInvitationCode)
        }
    }

    func testTooShortAndTooLongCodesAreStillRejected() {
        for input in ["abcdefghjkmnpq", "abcdefghjkmnpqrs"] {
            let formatted = InvitationCodeFormatter.formatted(input)
            XCTAssertThrowsError(try ReceiverManualPairing(serverURLString: server, invitationCode: formatted))
        }
    }
}
