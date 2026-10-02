import XCTest
@testable import HealthBridgeCompanionCore

final class IntakeMetadataAllowlistTests: XCTestCase {
    private let uuidText = "AAAAAAAA-1111-2222-3333-444455556666"

    private func full() -> [String: Any] {
        [
            "HealthRelayIntakeID": uuidText,
            "HealthRelayIntakeComponentID": "main-1",
            "HKSyncIdentifier": "sync-abc",
            "HKSyncVersion": NSNumber(value: 3),
        ]
    }

    func testAllFourKeysAreForwarded() {
        XCTAssertEqual(IntakeMetadataAllowlist.batchMetadata(from: full()), [
            "intake_id": uuidText.lowercased(),
            "intake_component_id": "main-1",
            "sync_identifier": "sync-abc",
            "sync_version": "3",
        ])
    }

    func testUnknownKeysAreDropped() {
        var input = full()
        input["HKFoodType"] = "Oatmeal"
        input["intake_id"] = uuidText
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertEqual(Set(result.keys), ["intake_id", "intake_component_id", "sync_identifier", "sync_version"])
        XCTAssertNil(result["HKFoodType"])
    }

    func testIntakeIdWithoutComponentIsDropped() {
        var input = full()
        input.removeValue(forKey: "HealthRelayIntakeComponentID")
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["intake_id"])
        XCTAssertNil(result["intake_component_id"])
    }

    func testComponentWithoutIntakeIdIsDropped() {
        var input = full()
        input.removeValue(forKey: "HealthRelayIntakeID")
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["intake_id"])
        XCTAssertNil(result["intake_component_id"])
    }

    func testUUIDIsLowercased() {
        XCTAssertEqual(IntakeMetadataAllowlist.batchMetadata(from: full())["intake_id"], uuidText.lowercased())
    }

    func testInvalidUUIDIsDropped() {
        var input = full()
        input["HealthRelayIntakeID"] = "not-a-uuid"
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["intake_id"])
        XCTAssertNil(result["intake_component_id"])
        XCTAssertEqual(result["sync_identifier"], "sync-abc")
    }

    func testComponentIdMustBeSlug() {
        for bad in ["", "Upper", "-lead", "has space", String(repeating: "a", count: 65)] {
            var input = full()
            input["HealthRelayIntakeComponentID"] = bad
            let result = IntakeMetadataAllowlist.batchMetadata(from: input)
            XCTAssertNil(result["intake_id"], bad)
            XCTAssertNil(result["intake_component_id"], bad)
        }
        var ok = full()
        ok["HealthRelayIntakeComponentID"] = String(repeating: "a", count: 64)
        XCTAssertNotNil(IntakeMetadataAllowlist.batchMetadata(from: ok)["intake_id"])
    }

    func testSyncVersionFromNSNumberIsForwardedAsDecimalText() {
        var input = full()
        input["HKSyncVersion"] = NSNumber(value: Int64.max)
        XCTAssertEqual(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"], "9223372036854775807")
        input["HKSyncVersion"] = 12
        XCTAssertEqual(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"], "12")
    }

    func testNonIntegralSyncVersionIsDropped() {
        var input = full()
        input["HKSyncVersion"] = NSNumber(value: 2.5)
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["sync_version"])
        XCTAssertNil(result["sync_identifier"])
    }

    func testSyncVersionZeroIsDropped() {
        var input = full()
        input["HKSyncVersion"] = NSNumber(value: 0)
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"])
        input["HKSyncVersion"] = NSNumber(value: -4)
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"])
    }

    func testSyncIdentifierLongerThan256IsDropped() {
        var input = full()
        input["HKSyncIdentifier"] = String(repeating: "x", count: 257)
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["sync_identifier"])
        XCTAssertNil(result["sync_version"])
        input["HKSyncIdentifier"] = String(repeating: "x", count: 256)
        XCTAssertNotNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_identifier"])
    }

    func testSyncKeysWithoutIntakeAreForwarded() {
        let result = IntakeMetadataAllowlist.batchMetadata(from: [
            "HKSyncIdentifier": "other-app-1",
            "HKSyncVersion": NSNumber(value: 1),
        ])
        XCTAssertEqual(result, ["sync_identifier": "other-app-1", "sync_version": "1"])
    }

    func testNonStringIntakeValuesAreDropped() {
        let result = IntakeMetadataAllowlist.batchMetadata(from: [
            "HealthRelayIntakeID": 42,
            "HealthRelayIntakeComponentID": 7,
            "HKSyncIdentifier": 9,
            "HKSyncVersion": "5",
        ])
        XCTAssertTrue(result.isEmpty)
    }

    func testEmptyMetadataGivesEmptyResult() {
        XCTAssertTrue(IntakeMetadataAllowlist.batchMetadata(from: [:]).isEmpty)
    }

    func testFactoryMergesIntakeMetadataIntoSampleMetadata() throws {
        let start = try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-15T07:30:00Z"))
        let sample = HealthKitQuantitySampleSummary(
            uuid: try XCTUnwrap(UUID(uuidString: "BBBBBBBB-1111-2222-3333-444455556666")),
            typeCode: "body_mass",
            start: start,
            end: start,
            value: 72.4,
            intakeMetadata: IntakeMetadataAllowlist.batchMetadata(from: full())
        )
        let batch = try XCTUnwrap(GenericQuantitySyncBatchFactory.makeQuantityBatch(
            samples: [sample],
            selectedTypeCodes: ["body_mass"],
            windowStart: try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-15T00:00:00Z")),
            windowEnd: try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-16T00:00:00Z")),
            generatedAt: try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-16T00:01:00Z"))
        ))
        let metadata = try XCTUnwrap(batch.samples.first?.metadata)
        XCTAssertEqual(metadata["intake_id"], uuidText.lowercased())
        XCTAssertEqual(metadata["sync_version"], "3")
        XCTAssertEqual(metadata["sample_kind"], "raw_quantity")
    }

    func testSyncVersionJustAboveInt64MaxIsDropped() {
        var input = full()
        input["HKSyncVersion"] = NSNumber(value: UInt64(Int64.max) + 1)
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["sync_version"])
        XCTAssertNil(result["sync_identifier"])
        input["HKSyncVersion"] = NSNumber(value: UInt64.max)
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"])
        input["HKSyncVersion"] = NSNumber(value: UInt64(Int64.max))
        XCTAssertEqual(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"], "9223372036854775807")
    }

    func testSyncVersionAboveInt64MaxIsDropped() {
        var input = full()
        input[IntakeMetadataAllowlist.syncVersionKey] = NSNumber(value: UInt64(Int64.max) + 1)
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"])
        input[IntakeMetadataAllowlist.syncVersionKey] = NSNumber(value: UInt64.max)
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"])
        input[IntakeMetadataAllowlist.syncVersionKey] = NSNumber(value: Int64.max)
        XCTAssertEqual(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_version"], "9223372036854775807")
    }

    func testSyncIdentifierLimitCountsUnicodeScalars() {
        var input = full()
        input[IntakeMetadataAllowlist.syncIdentifierKey] = String(repeating: "x", count: 256)
        XCTAssertNotNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_identifier"])
        input[IntakeMetadataAllowlist.syncIdentifierKey] = String(repeating: "x", count: 257)
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_identifier"])
        // "e" + combining acute: 2 scalars, 1 Character. 129 of them = 258 scalars, 129 Characters.
        let combining = String(repeating: "e\u{301}", count: 129)
        XCTAssertEqual(combining.count, 129)
        XCTAssertEqual(combining.unicodeScalars.count, 258)
        input[IntakeMetadataAllowlist.syncIdentifierKey] = combining
        XCTAssertNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_identifier"])
        input[IntakeMetadataAllowlist.syncIdentifierKey] = String(repeating: "e\u{301}", count: 128)
        XCTAssertNotNil(IntakeMetadataAllowlist.batchMetadata(from: input)["sync_identifier"])
    }

    func testFactoryDropsNonAllowlistedIntakeMetadata() throws {
        let metadata = try batch(withIntakeMetadata: [
            "intake_id": uuidText,
            "intake_component_id": "water",
            "foo": "bar",
            "intake_extra": "x",
        ])
        XCTAssertNil(metadata["foo"])
        XCTAssertNil(metadata["intake_extra"])
        XCTAssertEqual(metadata["intake_id"], uuidText.lowercased())
    }

    func testBooleanSyncVersionIsDropped() {
        var input = full()
        input["HKSyncVersion"] = NSNumber(value: true)
        let result = IntakeMetadataAllowlist.batchMetadata(from: input)
        XCTAssertNil(result["sync_version"])
        XCTAssertNil(result["sync_identifier"])
    }

    private func batch(withIntakeMetadata intakeMetadata: [String: String]) throws -> [String: String] {
        let start = try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-15T07:30:00Z"))
        let sample = HealthKitQuantitySampleSummary(
            uuid: try XCTUnwrap(UUID(uuidString: "BBBBBBBB-1111-2222-3333-444455556666")),
            typeCode: "body_mass",
            start: start,
            end: start,
            value: 72.4,
            intakeMetadata: intakeMetadata
        )
        let made = try XCTUnwrap(GenericQuantitySyncBatchFactory.makeQuantityBatch(
            samples: [sample],
            selectedTypeCodes: ["body_mass"],
            windowStart: try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-15T00:00:00Z")),
            windowEnd: try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-16T00:00:00Z")),
            generatedAt: try XCTUnwrap(HealthBridgeUTCFormatter.date(from: "2026-06-16T00:01:00Z"))
        ))
        return try XCTUnwrap(made.samples.first?.metadata)
    }

    func testFactoryDropsArbitraryIntakeMetadataStrings() throws {
        let metadata = try batch(withIntakeMetadata: [
            "intake_id": "not-a-uuid",
            "intake_component_id": "water",
            "foo": "bar",
        ])
        XCTAssertNil(metadata["intake_id"])
        XCTAssertNil(metadata["intake_component_id"])
        XCTAssertNil(metadata["foo"])
    }

    func testFactoryForwardsValidIntakeMetadataLowercased() throws {
        let metadata = try batch(withIntakeMetadata: [
            "intake_id": uuidText,
            "intake_component_id": "water",
            "sync_identifier": "sync-abc",
            "sync_version": "7",
        ])
        XCTAssertEqual(metadata["intake_id"], uuidText.lowercased())
        XCTAssertEqual(metadata["intake_component_id"], "water")
        XCTAssertEqual(metadata["sync_identifier"], "sync-abc")
        XCTAssertEqual(metadata["sync_version"], "7")
    }

    func testSanitizedRejectsBadSyncVersionText() {
        for bad in ["0", "01", "-1", "+1", "9223372036854775808", "1.5", "", "abc"] {
            let result = IntakeMetadataAllowlist.sanitized(batchMetadata: [
                "sync_identifier": "x", "sync_version": bad,
            ])
            XCTAssertTrue(result.isEmpty, bad)
        }
        XCTAssertEqual(
            IntakeMetadataAllowlist.sanitized(batchMetadata: ["sync_identifier": "x", "sync_version": "9223372036854775807"]),
            ["sync_identifier": "x", "sync_version": "9223372036854775807"]
        )
    }
}
