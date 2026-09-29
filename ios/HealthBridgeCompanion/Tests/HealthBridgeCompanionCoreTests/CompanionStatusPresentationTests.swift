import XCTest
@testable import HealthBridgeCompanionCore

/// Pins the substring rules the status and connection cards used before they moved into Core.
final class CompanionStatusPresentationTests: XCTestCase {
    func testSyncErrorTitleForKnownMessages() {
        let cases: [(String, String)] = [
            ("Step sync failed: timed out", "Sync Failed"),
            ("Sync stopped: Local bridge check failed: timed out", "Sync Failed"),
            ("Receiver test failed: timed out", "Connection Failed"),
            ("Local bridge check failed: timed out", "Connection Failed"),
            ("Bridge URL is invalid.", "Connection Failed"),
            ("Encrypted iCloud mailbox check failed: unavailable", "Needs Attention"),
            ("Something unexpected happened.", "Needs Attention"),
        ]
        for (message, title) in cases {
            XCTAssertEqual(CompanionStatusPresentation.syncErrorTitle(message: message), title, message)
        }
    }

    func testSyncErrorTitleAccidentalSubstrings() {
        // "sync" wins over "server": a sleep failure mentioning the server is a Sync failure.
        XCTAssertEqual(
            CompanionStatusPresentation.syncErrorTitle(message: "Anchored sleep sync failed: could not reach server"),
            "Sync Failed"
        )
        // A connection failure whose detail mentions queued uploads reads as a Sync failure.
        XCTAssertEqual(
            CompanionStatusPresentation.syncErrorTitle(
                message: "Receiver test failed: Queued uploads cannot be verified for this server."
            ),
            "Sync Failed"
        )
        XCTAssertEqual(CompanionStatusPresentation.syncErrorTitle(message: "SYNC trouble"), "Sync Failed")
    }

    func testConnectionIsReachable() {
        let reachable = CompanionStatusPresentation.connectionIsReachable
        XCTAssertTrue(reachable("Connection check passed with HTTP 200.", false))
        XCTAssertTrue(reachable("Server connected", false))
        XCTAssertTrue(reachable("Local bridge verified", false))
        XCTAssertTrue(reachable("Connected to local bridge", false))
        XCTAssertFalse(reachable("Connection check passed with HTTP 200.", true))
        XCTAssertFalse(reachable("Receiver accepted test batch via host with HTTP 200. Pending outbox: 0.", false))
        XCTAssertFalse(reachable("Connected: Home. The device credential is stored securely on this iPhone.", false))
        XCTAssertFalse(reachable("Mailbox folder is ready on this iPhone. Receiver delivery has not been verified.", false))
    }

    func testMailboxFolderIsReady() {
        let text = "Mailbox folder is ready on this iPhone. Receiver delivery has not been verified."
        XCTAssertTrue(CompanionStatusPresentation.mailboxFolderIsReady(message: text, isError: false, usesMailbox: true))
        XCTAssertFalse(CompanionStatusPresentation.mailboxFolderIsReady(message: text, isError: false, usesMailbox: false))
        XCTAssertFalse(CompanionStatusPresentation.mailboxFolderIsReady(message: text, isError: true, usesMailbox: true))
        XCTAssertFalse(CompanionStatusPresentation.mailboxFolderIsReady(
            message: text.lowercased(), isError: false, usesMailbox: true
        ))
    }

    func testConnectionNoticeShownForConnectionRelatedMessages() {
        let shown = [
            "Connection check passed with HTTP 200.",
            "Local bridge check failed: timed out",
            "Receiver test failed: timed out",
            "Bridge URL is invalid.",
            "Mailbox folder is ready on this iPhone. Receiver delivery has not been verified.",
            "Disconnected and turned automatic sync off. Queued uploads: 0.",
            // Accidental: a sync failure that happens to mention the receiver.
            "Anchored sleep sync failed: could not reach receiver",
        ]
        for message in shown {
            let notice = CompanionStatusPresentation.connectionNotice(message: message, isError: false)
            XCTAssertEqual(
                notice,
                CompanionPrimaryStatusMessage.sanitized(from: message, isError: false),
                message
            )
            XCTAssertFalse(notice.isEmpty, message)
        }
    }

    func testConnectionNoticeHiddenOtherwise() {
        let hidden = [
            "",
            "   ",
            "Reading anchored Step Count changes from HealthKit...",
            "Step sync failed: timed out",
            "Apple Health permission failed.",
            // Accidental: "apple health" suppresses a message that also says "connection".
            "Sleep sync failed: Apple Health connection lost",
        ]
        for message in hidden {
            XCTAssertEqual(CompanionStatusPresentation.connectionNotice(message: message, isError: true), "", message)
        }
    }
}
