import Foundation
import XCTest
@testable import HealthBridgeCompanionCore

final class LastSyncedTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func line(secondsAgo: TimeInterval) -> String? {
        LastSyncedFormatter.line(lastSyncedAt: now.addingTimeInterval(-secondsAgo), now: now)
    }

    func testNoSyncYieldsNoLine() {
        XCTAssertNil(LastSyncedFormatter.line(lastSyncedAt: nil, now: now))
    }

    func testRelativeBuckets() {
        XCTAssertEqual(line(secondsAgo: 5), "Last synced just now")
        XCTAssertEqual(line(secondsAgo: 59), "Last synced just now")
        XCTAssertEqual(line(secondsAgo: 60), "Last synced 1 minute ago")
        XCTAssertEqual(line(secondsAgo: 5 * 60 + 20), "Last synced 5 minutes ago")
        XCTAssertEqual(line(secondsAgo: 60 * 60), "Last synced 1 hour ago")
        XCTAssertEqual(line(secondsAgo: 3 * 3600 + 100), "Last synced 3 hours ago")
        XCTAssertEqual(line(secondsAgo: 24 * 3600), "Last synced yesterday")
        XCTAssertEqual(line(secondsAgo: 3 * 86400 + 5), "Last synced 3 days ago")
    }

    func testFutureTimestampDoesNotGoNegative() {
        XCTAssertEqual(line(secondsAgo: -3600), "Last synced just now")
    }

    func testStoreRoundTripsAndStartsEmpty() throws {
        let suite = "LastSyncedTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        addTeardownBlock { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }
        let store = LastSyncedStore(userDefaults: defaults)

        XCTAssertNil(store.lastSyncedAt)
        store.record(now)
        XCTAssertEqual(LastSyncedStore(userDefaults: defaults).lastSyncedAt, now)
    }
}
