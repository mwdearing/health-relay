import XCTest
@testable import HealthBridgeCompanionCore

final class MedicationDoseEventSyncBatchFactoryTests: XCTestCase {
    func testFactoryBuildsStableRecordsForReceiverContract() throws {
        let generatedAt = try date("2026-06-10T08:00:00Z")
        let windowStart = try date("2026-06-01T00:00:00Z")
        let windowEnd = try date("2026-06-10T08:00:00Z")
        let eventID = try XCTUnwrap(UUID(uuidString: "C1D2E3F4-AAAA-BBBB-CCCC-DDDDEEEEFFFF"))
        let events = [
            HealthKitMedicationDoseEventSummary(
                uuid: eventID,
                medicationName: "Synthetic Vitamin",
                conceptKey: "c0123456789ab",
                status: .taken,
                statusRaw: 1,
                start: try date("2026-06-09T13:05:00Z"),
                scheduled: try date("2026-06-09T13:00:00Z"),
                dose: 1,
                unit: "count"
            )
        ]

        let batch = MedicationDoseEventSyncBatchFactory.makeMedicationDoseEventBatch(
            events: events,
            windowStart: windowStart,
            windowEnd: windowEnd,
            generatedAt: generatedAt
        )

        XCTAssertEqual(batch.healthTypes, [.medicationDoseEvents])
        XCTAssertTrue(batch.samples.isEmpty)
        XCTAssertTrue(batch.electrocardiograms.isEmpty)
        XCTAssertEqual(batch.medicationDoseEvents.count, 1)
        let record = try XCTUnwrap(batch.medicationDoseEvents.first)
        XCTAssertEqual(record.clientRecordID, "hk-meddose-c1d2e3f4-aaaa-bbbb-cccc-ddddeeeeffff")
        XCTAssertEqual(record.sourceKey, "apple_health.phone")
        XCTAssertEqual(record.medicationName, "Synthetic Vitamin")
        XCTAssertEqual(record.medicationConceptKey, "c0123456789ab")
        XCTAssertEqual(record.status, .taken)
        XCTAssertEqual(record.statusRaw, 1)
        XCTAssertEqual(record.startTime, "2026-06-09T13:05:00Z")
        XCTAssertEqual(record.scheduledTime, "2026-06-09T13:00:00Z")
        XCTAssertEqual(record.dose, 1)
        XCTAssertEqual(record.unit, "count")
        XCTAssertEqual(batch.sync.cursors, [
            HealthBridgeSyncCursor(
                sourceKey: "apple_health.phone",
                cursorKind: "foreground_medication_dose_event_sync",
                cursorValue: "2026-06-10T08:00:00Z"
            )
        ])
    }

    func testRecordsDropNegativeDoseAndEmptyUnitAndNameUnnamedMedications() throws {
        let records = MedicationDoseEventSyncBatchFactory.medicationDoseEventRecords(
            from: [
                HealthKitMedicationDoseEventSummary(
                    uuid: UUID(),
                    medicationName: "",
                    conceptKey: nil,
                    status: .skipped,
                    statusRaw: 2,
                    start: try date("2026-06-09T13:05:00Z"),
                    scheduled: nil,
                    dose: -1,
                    unit: ""
                )
            ],
            source: HealthBridgeAppleHealthSource.phone
        )

        let record = try XCTUnwrap(records.first)
        XCTAssertEqual(record.medicationName, "unnamed medication")
        XCTAssertNil(record.medicationConceptKey)
        XCTAssertNil(record.dose)
        XCTAssertNil(record.unit)
        XCTAssertNil(record.scheduledTime)
    }

    func testEncoderOmitsKeyWhenEmptyAndRoundTripsWhenPresent() throws {
        let windowStart = try date("2026-06-01T00:00:00Z")
        let windowEnd = try date("2026-06-10T08:00:00Z")
        let empty = MedicationDoseEventSyncBatchFactory.makeMedicationDoseEventBatch(
            events: [], windowStart: windowStart, windowEnd: windowEnd, generatedAt: windowEnd
        )
        let emptyData = try HealthBridgeBatchEncoder().encode(empty)
        XCTAssertFalse(try XCTUnwrap(String(data: emptyData, encoding: .utf8)).contains("\"medication_dose_events\""))
        XCTAssertEqual(try JSONDecoder().decode(HealthBridgeBatchV1.self, from: emptyData), empty)

        let populated = MedicationDoseEventSyncBatchFactory.makeMedicationDoseEventBatch(
            events: [
                HealthKitMedicationDoseEventSummary(
                    uuid: UUID(), medicationName: "Synthetic Vitamin", conceptKey: "c0123456789ab",
                    status: .notLogged, statusRaw: 5, start: try date("2026-06-09T13:05:00Z"),
                    scheduled: nil, dose: nil, unit: nil
                )
            ],
            windowStart: windowStart, windowEnd: windowEnd, generatedAt: windowEnd
        )
        let data = try HealthBridgeBatchEncoder().encode(populated)
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))
        XCTAssertTrue(text.contains("\"medication_dose_events\":[{"))
        XCTAssertTrue(text.contains("\"status\":\"not_logged\""))
        XCTAssertTrue(text.contains("\"medication_concept_key\":\"c0123456789ab\""))
        XCTAssertEqual(try JSONDecoder().decode(HealthBridgeBatchV1.self, from: data), populated)
    }

    func testConceptKeyIsStableAndShort() {
        let key = HealthBridgeConceptKey.make(archived: Data("same-bytes".utf8))
        XCTAssertEqual(key, HealthBridgeConceptKey.make(archived: Data("same-bytes".utf8)))
        XCTAssertEqual(key.count, 13)
        XCTAssertTrue(key.hasPrefix("c"))
        XCTAssertNotEqual(key, HealthBridgeConceptKey.make(archived: Data("other".utf8)))
    }

    private func date(_ value: String) throws -> Date {
        try XCTUnwrap(HealthBridgeUTCFormatter.date(from: value))
    }
}
