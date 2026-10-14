import Foundation
import XCTest
@testable import HealthBridgeCompanionCore

final class SyncConsentPolicyTests: XCTestCase {
    private let acceptedAt = Date(timeIntervalSince1970: 1_700_000_000)

    func testNoRecordMeansNoSend() {
        XCTAssertFalse(SyncConsentPolicy.maySend(record: nil, currentVersion: 1))
    }

    func testAcceptedRecordForCurrentVersionAllowsSend() {
        let record = SyncConsentRecord(version: 1, acceptedAt: acceptedAt)
        XCTAssertTrue(SyncConsentPolicy.maySend(record: record, currentVersion: 1))
    }

    func testRecordForOlderCopyDoesNotAllowSend() {
        let record = SyncConsentRecord(version: 1, acceptedAt: acceptedAt)
        XCTAssertFalse(SyncConsentPolicy.maySend(record: record, currentVersion: 2))
    }

    func testStoreStartsWithoutConsentAndAcceptPersistsAcrossInstances() throws {
        let suite = "SyncConsentPolicyTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        let first = SyncConsentStore(userDefaults: defaults)
        XCTAssertFalse(first.maySend)

        first.accept(now: acceptedAt)
        XCTAssertTrue(first.maySend)

        let reopened = SyncConsentStore(userDefaults: defaults)
        XCTAssertTrue(reopened.maySend)
        XCTAssertEqual(reopened.record?.acceptedAt, acceptedAt)
        XCTAssertEqual(reopened.record?.version, SyncConsentPolicy.currentVersion)
    }

    func testWithdrawClearsConsentSoSendIsBlockedAgain() throws {
        let suite = "SyncConsentPolicyTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        let store = SyncConsentStore(userDefaults: defaults)
        store.accept(now: acceptedAt)
        store.withdraw()
        XCTAssertFalse(store.maySend)
        XCTAssertNil(SyncConsentStore(userDefaults: defaults).record)
    }
}

/// A persisted store that admits sending, for tests that drive real egress primitives.
func admittingSyncConsentStore() -> SyncConsentStore {
    let suite = "SyncConsentAdmitting.\(UUID().uuidString)"
    let store = SyncConsentStore(userDefaults: UserDefaults(suiteName: suite) ?? .standard)
    store.accept()
    return store
}
