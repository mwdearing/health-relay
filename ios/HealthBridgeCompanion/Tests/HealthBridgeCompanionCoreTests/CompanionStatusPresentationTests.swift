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
        let reachable: (String, Bool) -> Bool = CompanionStatusPresentation.connectionIsReachable(message:isError:)
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

    // MARK: - Typed status

    /// Every converted writer, with the kind it sets and the exact text it writes.
    private let converted: [(CompanionStatus, String, isError: Bool)] = [
        (.mailboxFolderReady, "Mailbox folder is ready on this iPhone. Receiver delivery has not been verified.", false),
        (.connectionFailed, "Encrypted iCloud mailbox check failed: unavailable", true),
        (.connectionFailed, "Bridge URL is invalid.", true),
        (.connectionVerified, "Connection check passed with HTTP 200.", false),
        (.connectionFailed, "Local bridge check failed: timed out", true),
        (.connectionFailed, "Receiver test failed: timed out", true),
        (.syncFailed(.steps), "Step sync failed: timed out", true),
        (.syncFailed(.dailyActivity), "Daily activity total sync failed: timed out", true),
        (.syncFailed(.workouts), "Workout sync failed: timed out", true),
        (.syncFailed(.workouts), "Anchored workout sync failed: timed out", true),
        (.syncFailed(.sleep), "Anchored sleep sync failed: could not reach server", true),
        (.syncFailed(.otherMetrics), "Supported quantity sync failed: no metric could be read", true),
        (.syncFailed(.otherMetrics), "Background quantity sync failed: no metric could be read", true),
    ]

    /// Texts where the typed result intentionally differs from the old text-only result.
    /// Anything not listed here must match the legacy result exactly.
    private let intendedTitleDifferences: Set<String> = [
        // No "sync"/"connection"/"bridge"/"server"/"receiver": legacy said "Needs Attention".
        "Encrypted iCloud mailbox check failed: unavailable",
    ]

    func testTypedTitleMatchesLegacyExceptListedDifferences() {
        for (status, message, isError) in converted where isError {
            let typed = CompanionStatusPresentation.syncErrorTitle(status: status, message: message)
            let legacy = CompanionStatusPresentation.syncErrorTitle(message: message)
            if intendedTitleDifferences.contains(message) {
                XCTAssertNotEqual(typed, legacy, message)
            } else {
                XCTAssertEqual(typed, legacy, message)
            }
        }
    }

    func testTypedTitleIntendedDifferences() {
        XCTAssertEqual(
            CompanionStatusPresentation.syncErrorTitle(
                status: .connectionFailed,
                message: "Encrypted iCloud mailbox check failed: unavailable"
            ),
            "Connection Failed"
        )
        // A connection failure whose detail mentions queued uploads is no longer a Sync failure.
        XCTAssertEqual(
            CompanionStatusPresentation.syncErrorTitle(
                status: .connectionFailed,
                message: "Receiver test failed: Queued uploads cannot be verified for this server."
            ),
            "Connection Failed"
        )
        XCTAssertEqual(
            CompanionStatusPresentation.syncErrorTitle(
                status: .connectionFailed,
                message: "Local bridge check failed: sync state unreadable"
            ),
            "Connection Failed"
        )
    }

    func testNoticeStatusUsesLegacyTitleAndNotice() {
        for message in ["Step sync failed: x", "Receiver test failed: x", "Something unexpected", "Bridge URL is invalid."] {
            XCTAssertEqual(
                CompanionStatusPresentation.syncErrorTitle(status: .notice, message: message),
                CompanionStatusPresentation.syncErrorTitle(message: message)
            )
            XCTAssertEqual(
                CompanionStatusPresentation.connectionNotice(status: .notice, message: message, isError: true),
                CompanionStatusPresentation.connectionNotice(message: message, isError: true)
            )
        }
    }

    func testTypedNoticeMatchesLegacyExceptListedDifferences() {
        // Differences: none of the converted texts is hidden by the legacy keyword or
        // permission rules, except the ones listed here, which the typed cases now show.
        let intendedNoticeDifferences: Set<String> = []
        for (status, message, isError) in converted {
            let typed = CompanionStatusPresentation.connectionNotice(status: status, message: message, isError: isError)
            let legacy = CompanionStatusPresentation.connectionNotice(message: message, isError: isError)
            if intendedNoticeDifferences.contains(message) {
                XCTAssertNotEqual(typed, legacy, message)
            } else {
                XCTAssertEqual(typed, legacy, message)
            }
        }
    }

    func testTypedConnectionCasesShowNoticeEvenWhenLegacyWouldHideIt() {
        // Intended difference: the legacy permission/keyword gates no longer apply to
        // connection outcomes.
        let message = "Local bridge check failed: Apple Health permission screen was open"
        XCTAssertEqual(CompanionStatusPresentation.connectionNotice(message: message, isError: true), "")
        XCTAssertFalse(
            CompanionStatusPresentation.connectionNotice(status: .connectionFailed, message: message, isError: true).isEmpty
        )
    }

    func testReachableAndMailboxReadyUseOnlyTheTypedCase() {
        let reachable: (CompanionStatus, Bool) -> Bool = CompanionStatusPresentation.connectionIsReachable(status:isError:)
        XCTAssertTrue(reachable(.connectionVerified, false))
        XCTAssertFalse(reachable(.connectionVerified, true))
        for status in [CompanionStatus.notice, .mailboxFolderReady, .connectionFailed, .syncFailed(.steps)] {
            XCTAssertFalse(reachable(status, false))
        }
        let ready: (CompanionStatus, Bool, Bool) -> Bool = CompanionStatusPresentation.mailboxFolderIsReady(status:isError:usesMailbox:)
        XCTAssertTrue(ready(.mailboxFolderReady, false, true))
        XCTAssertFalse(ready(.mailboxFolderReady, false, false))
        XCTAssertFalse(ready(.mailboxFolderReady, true, true))
        XCTAssertFalse(ready(.notice, false, true))
        XCTAssertFalse(ready(.connectionVerified, false, true))
    }

    func testEveryConvertedTextThatLegacyCalledReachableIsConnectionVerified() {
        // Guards against a writer of a "reachable" phrase being left as a plain notice.
        for (status, message, isError) in converted {
            let legacy = CompanionStatusPresentation.connectionIsReachable(message: message, isError: isError)
            let typed = CompanionStatusPresentation.connectionIsReachable(status: status, isError: isError)
            XCTAssertEqual(typed, legacy, message)
            let legacyReady = CompanionStatusPresentation.mailboxFolderIsReady(
                message: message, isError: isError, usesMailbox: true
            )
            let typedReady = CompanionStatusPresentation.mailboxFolderIsReady(
                status: status, isError: isError, usesMailbox: true
            )
            XCTAssertEqual(typedReady, legacyReady, message)
        }
    }
}
