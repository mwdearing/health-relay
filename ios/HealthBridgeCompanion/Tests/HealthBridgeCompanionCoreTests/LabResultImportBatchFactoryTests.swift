import XCTest
@testable import HealthBridgeCompanionCore

final class LabResultImportBatchFactoryTests: XCTestCase {
    func testLargeImportSplitsIntoBatchesUnderReceiverLimit() throws {
        let results = (0..<3_000).map { index in
            HealthBridgeLabResult(
                clientRecordID: "synthetic-lab-\(index)",
                sourceKey: AppleHealthExportLabImporter.sourceKey,
                loinc: "0000-0",
                name: "Synthetic analyte \(index)",
                category: "Synthetic panel",
                effectiveDate: "2026-01-01",
                valueNum: Double(index) / 8.0,
                unit: "mg/dL",
                valueText: String(repeating: "synthetic note ", count: 40),
                refLow: 1.0,
                refHigh: 9.0,
                refText: "1.0 - 9.0"
            )
        }
        let single = LabResultImportBatchFactory.makeLabResultBatch(
            results: results,
            generatedAt: Date(timeIntervalSince1970: 1_780_000_000)
        )

        let batches = LabResultImportBatchFactory.boundedBatches(from: single)

        XCTAssertGreaterThan(batches.count, 1)
        for batch in batches {
            XCTAssertLessThanOrEqual(
                try HealthBridgeBatchEncoder().encode(batch).count,
                OutboundBatchSizePolicy.receiverMaxBatchBytes
            )
            XCTAssertLessThanOrEqual(batch.labResults.count, LabResultImportBatchFactory.maxResultsPerBatch)
        }
        XCTAssertEqual(batches.flatMap(\.labResults), results)
    }

    func testSmallImportIsReturnedUnchanged() {
        let single = LabResultImportBatchFactory.makeLabResultBatch(results: [])

        XCTAssertEqual(LabResultImportBatchFactory.boundedBatches(from: single), [single])
    }
}
