import Foundation
import XCTest
@testable import HealthBridgeCompanionCore

final class RetryAfterBackoffTests: XCTestCase {
    func testRetryAfterIsReadOnlyOn429WithWholeSeconds() {
        XCTAssertEqual(
            BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 429, headerValue: "120"),
            120
        )
        XCTAssertEqual(
            BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 429, headerValue: " 7 "),
            7
        )
    }

    func testRetryAfterIsClampedToOneHour() {
        XCTAssertEqual(
            BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 429, headerValue: "86400"),
            3600
        )
    }

    func testRetryAfterIgnoresOtherStatusesAndUnusableValues() {
        XCTAssertNil(BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 503, headerValue: "30"))
        XCTAssertNil(BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 429, headerValue: nil))
        XCTAssertNil(BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 429, headerValue: "0"))
        XCTAssertNil(BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(statusCode: 429, headerValue: "-5"))
        XCTAssertNil(
            BackgroundOutboxUploadCompletionPolicy.retryAfterSeconds(
                statusCode: 429,
                headerValue: "Wed, 21 Oct 2026 07:00:00 GMT"
            )
        )
    }

    func testCompletionWithoutRetryAfterStillDecodesFromOlderRecords() throws {
        let legacy = Data(#"{"statusCode":200,"hadTransportError":false}"#.utf8)
        let decoded = try JSONDecoder().decode(BackgroundUploadTaskCompletion.self, from: legacy)
        XCTAssertNil(decoded.retryAfterSeconds)
        XCTAssertEqual(decoded.statusCode, 200)
    }

    func testSchedulerHonoursNotBeforeFloorOnlyWhenLater() {
        let now = Date(timeIntervalSince1970: 1_000)
        let floor = now.addingTimeInterval(3600)
        XCTAssertEqual(
            HealthBridgeBackgroundSync.nextEarliestBeginDate(enabled: true, now: now, notBefore: floor),
            floor
        )
        let past = now.addingTimeInterval(-60)
        XCTAssertEqual(
            HealthBridgeBackgroundSync.nextEarliestBeginDate(enabled: true, now: now, notBefore: past),
            HealthBridgeBackgroundSync.nextEarliestBeginDate(enabled: true, now: now)
        )
    }

    func testRetryDeadlineIsStoredThenClearedOncePast() throws {
        let suite = "retry-deadline-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let now = Date(timeIntervalSince1970: 2_000)

        BackgroundUploadRetryDeadline.note(retryAfterSeconds: 90, now: now, defaults: defaults)
        XCTAssertEqual(
            BackgroundUploadRetryDeadline.pending(now: now.addingTimeInterval(10), defaults: defaults),
            now.addingTimeInterval(90)
        )
        XCTAssertNil(BackgroundUploadRetryDeadline.pending(now: now.addingTimeInterval(91), defaults: defaults))
        XCTAssertNil(defaults.object(forKey: BackgroundUploadRetryDeadline.defaultsKey))
    }
}
