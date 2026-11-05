import XCTest
@testable import HealthBridgeCompanionCore

final class ElectrocardiogramSyncBatchFactoryTests: XCTestCase {
    func testFactoryBuildsStableElectrocardiogramRecordsForReceiverContract() throws {
        let generatedAt = try date("2026-06-10T08:00:00Z")
        let windowStart = try date("2026-06-01T00:00:00Z")
        let windowEnd = try date("2026-06-10T08:00:00Z")
        let recordingID = try XCTUnwrap(UUID(uuidString: "B7D2E1F0-AAAA-BBBB-CCCC-DDDDEEEEFFFF"))
        let recordings = [
            HealthKitElectrocardiogramSummary(
                uuid: recordingID,
                start: try date("2026-06-09T07:15:00Z"),
                end: try date("2026-06-09T07:15:30Z"),
                classification: .sinusRhythm,
                symptomsStatus: .noneReported,
                averageHeartRateBPM: 62,
                samplingFrequencyHz: 512,
                voltageCount: 4,
                voltagesMicrovolts: [-12.5, 3.0, 41.75, 0.0]
            )
        ]

        let batch = ElectrocardiogramSyncBatchFactory.makeElectrocardiogramBatch(
            recordings: recordings,
            windowStart: windowStart,
            windowEnd: windowEnd,
            generatedAt: generatedAt
        )

        XCTAssertEqual(batch.healthTypes, [.electrocardiogram])
        XCTAssertTrue(batch.samples.isEmpty)
        XCTAssertTrue(batch.workouts.isEmpty)
        XCTAssertEqual(batch.electrocardiograms.count, 1)
        let record = try XCTUnwrap(batch.electrocardiograms.first)
        XCTAssertEqual(record.clientRecordID, "hk-ecg-b7d2e1f0-aaaa-bbbb-cccc-ddddeeeeffff")
        XCTAssertEqual(record.sourceKey, "apple_health.phone")
        XCTAssertEqual(record.startTime, "2026-06-09T07:15:00Z")
        XCTAssertEqual(record.endTime, "2026-06-09T07:15:30Z")
        XCTAssertEqual(record.classification, .sinusRhythm)
        XCTAssertEqual(record.symptomsStatus, .noneReported)
        XCTAssertEqual(record.averageHeartRateBPM, 62)
        XCTAssertEqual(record.samplingFrequencyHz, 512)
        XCTAssertEqual(record.voltageCount, 4)
        XCTAssertEqual(record.voltagesMicrovolts, [-12.5, 3.0, 41.75, 0.0])
        XCTAssertEqual(batch.sync.cursors, [
            HealthBridgeSyncCursor(
                sourceKey: "apple_health.phone",
                cursorKind: "foreground_electrocardiogram_sync",
                cursorValue: "2026-06-10T08:00:00Z"
            )
        ])
    }

    func testMismatchedOrNonFiniteVoltageSeriesFallsBackToSummaryOnly() throws {
        let recordings = [
            HealthKitElectrocardiogramSummary(
                uuid: UUID(),
                start: try date("2026-06-09T07:15:00Z"),
                end: try date("2026-06-09T07:15:30Z"),
                classification: .inconclusiveOther,
                symptomsStatus: .notSet,
                averageHeartRateBPM: nil,
                samplingFrequencyHz: nil,
                voltageCount: 3,
                voltagesMicrovolts: [1.0, 2.0]
            ),
            HealthKitElectrocardiogramSummary(
                uuid: UUID(),
                start: try date("2026-06-09T08:15:00Z"),
                end: try date("2026-06-09T08:15:30Z"),
                classification: .atrialFibrillation,
                symptomsStatus: .present,
                averageHeartRateBPM: 98,
                samplingFrequencyHz: 512,
                voltageCount: 2,
                voltagesMicrovolts: [1.0, .nan]
            ),
        ]

        let records = ElectrocardiogramSyncBatchFactory.electrocardiogramRecords(
            from: recordings,
            source: HealthBridgeAppleHealthSource.phone
        )

        XCTAssertEqual(records.count, 2)
        XCTAssertNil(records[0].voltagesMicrovolts)
        XCTAssertEqual(records[0].voltageCount, 3)
        XCTAssertNil(records[1].voltagesMicrovolts)
        XCTAssertEqual(records[1].classification, .atrialFibrillation)
    }

    func testEncoderOmitsElectrocardiogramsKeyWhenEmptyAndRoundTripsWhenPresent() throws {
        let generatedAt = try date("2026-06-10T08:00:00Z")
        let windowStart = try date("2026-06-01T00:00:00Z")
        let windowEnd = try date("2026-06-10T08:00:00Z")

        let empty = ElectrocardiogramSyncBatchFactory.makeElectrocardiogramBatch(
            recordings: [],
            windowStart: windowStart,
            windowEnd: windowEnd,
            generatedAt: generatedAt
        )
        let emptyData = try HealthBridgeBatchEncoder().encode(empty)
        let emptyText = try XCTUnwrap(String(data: emptyData, encoding: .utf8))
        XCTAssertFalse(emptyText.contains("\"electrocardiograms\""))
        XCTAssertEqual(try JSONDecoder().decode(HealthBridgeBatchV1.self, from: emptyData), empty)

        let recording = HealthKitElectrocardiogramSummary(
            uuid: UUID(),
            start: try date("2026-06-09T07:15:00Z"),
            end: try date("2026-06-09T07:15:30Z"),
            classification: .sinusRhythm,
            symptomsStatus: .noneReported,
            averageHeartRateBPM: 60,
            samplingFrequencyHz: 512,
            voltageCount: 2,
            voltagesMicrovolts: [0.5, -0.5]
        )
        let populated = ElectrocardiogramSyncBatchFactory.makeElectrocardiogramBatch(
            recordings: [recording],
            windowStart: windowStart,
            windowEnd: windowEnd,
            generatedAt: generatedAt
        )
        let populatedData = try HealthBridgeBatchEncoder().encode(populated)
        let populatedText = try XCTUnwrap(String(data: populatedData, encoding: .utf8))
        XCTAssertTrue(populatedText.contains("\"electrocardiograms\":[{"))
        XCTAssertTrue(populatedText.contains("\"classification\":\"sinus_rhythm\""))
        XCTAssertTrue(populatedText.contains("\"symptoms_status\":\"none\""))
        XCTAssertTrue(populatedText.contains("\"voltages_microvolts\":[0.5,-0.5]"))
        XCTAssertEqual(try JSONDecoder().decode(HealthBridgeBatchV1.self, from: populatedData), populated)
    }

    func testManyFullVoltageRecordingsSplitIntoBatchesUnderReceiverLimit() throws {
        let windowStart = try date("2020-01-01T00:00:00Z")
        let windowEnd = try date("2026-06-10T08:00:00Z")
        let base = try date("2026-01-01T00:00:00Z")
        // 20 synthetic recordings of about 15k float-derived voltages encode to well over
        // the receiver's 5,000,000-byte body limit as a single batch.
        let recordings = (0..<20).map { index in
            syntheticRecording(
                index: index,
                start: base.addingTimeInterval(Double(index) * 3_600),
                voltageCount: 15_360
            )
        }
        let single = ElectrocardiogramSyncBatchFactory.makeElectrocardiogramBatch(
            recordings: recordings,
            windowStart: windowStart,
            windowEnd: windowEnd
        )
        XCTAssertGreaterThan(
            try HealthBridgeBatchEncoder().encode(single).count,
            OutboundBatchSizePolicy.receiverMaxBatchBytes
        )

        let batches = ElectrocardiogramSyncBatchFactory.boundedBatches(from: single)

        XCTAssertGreaterThan(batches.count, 1)
        for batch in batches {
            XCTAssertLessThanOrEqual(
                try HealthBridgeBatchEncoder().encode(batch).count,
                OutboundBatchSizePolicy.receiverMaxBatchBytes
            )
        }
        XCTAssertEqual(
            batches.flatMap(\.electrocardiograms).map(\.clientRecordID),
            single.electrocardiograms.map(\.clientRecordID)
        )
        XCTAssertTrue(batches.flatMap(\.electrocardiograms).allSatisfy { $0.voltagesMicrovolts != nil })
        // Only the last batch carries the cursor, so the checkpoint applies after the final upload.
        XCTAssertEqual(batches.dropLast().map(\.sync.cursors), Array(repeating: [], count: batches.count - 1))
        XCTAssertEqual(batches.last?.sync.cursors, single.sync.cursors)
    }

    func testSingleRecordingOverTheByteBudgetIsSentSummaryOnly() throws {
        let windowStart = try date("2020-01-01T00:00:00Z")
        let windowEnd = try date("2026-06-10T08:00:00Z")
        let oversized = syntheticRecording(
            index: 1,
            start: try date("2026-02-01T00:00:00Z"),
            voltageCount: 400_000
        )
        let small = syntheticRecording(
            index: 2,
            start: try date("2026-02-02T00:00:00Z"),
            voltageCount: 8
        )
        let single = ElectrocardiogramSyncBatchFactory.makeElectrocardiogramBatch(
            recordings: [oversized, small],
            windowStart: windowStart,
            windowEnd: windowEnd
        )

        let batches = ElectrocardiogramSyncBatchFactory.boundedBatches(from: single)

        for batch in batches {
            XCTAssertLessThanOrEqual(
                try HealthBridgeBatchEncoder().encode(batch).count,
                OutboundBatchSizePolicy.receiverMaxBatchBytes
            )
        }
        let records = batches.flatMap(\.electrocardiograms)
        XCTAssertEqual(records.count, 2)
        XCTAssertNil(records[0].voltagesMicrovolts)
        XCTAssertEqual(records[0].voltageCount, 400_000)
        XCTAssertEqual(records[1].voltagesMicrovolts?.count, 8)
        XCTAssertEqual(batches.last?.sync.cursors, single.sync.cursors)
    }

    func testBatchThatAlreadyFitsIsReturnedUnchanged() throws {
        let single = ElectrocardiogramSyncBatchFactory.makeElectrocardiogramBatch(
            recordings: [
                syntheticRecording(index: 3, start: try date("2026-03-01T00:00:00Z"), voltageCount: 16)
            ],
            windowStart: try date("2026-03-01T00:00:00Z"),
            windowEnd: try date("2026-03-02T00:00:00Z")
        )

        XCTAssertEqual(ElectrocardiogramSyncBatchFactory.boundedBatches(from: single), [single])
    }

    private func syntheticRecording(
        index: Int,
        start: Date,
        voltageCount: Int
    ) -> HealthKitElectrocardiogramSummary {
        // Float-derived values encode with long decimal tails, the worst case for size.
        let voltages = (0..<voltageCount).map { sample in
            Double(Float(sin(Double(sample + index) * 0.013) * 480.0))
        }
        return HealthKitElectrocardiogramSummary(
            uuid: UUID(),
            start: start,
            end: start.addingTimeInterval(30),
            classification: .sinusRhythm,
            symptomsStatus: .notSet,
            averageHeartRateBPM: 60,
            samplingFrequencyHz: 512,
            voltageCount: voltageCount,
            voltagesMicrovolts: voltages
        )
    }

    private func date(_ value: String) throws -> Date {
        try XCTUnwrap(HealthBridgeUTCFormatter.date(from: value))
    }
}
