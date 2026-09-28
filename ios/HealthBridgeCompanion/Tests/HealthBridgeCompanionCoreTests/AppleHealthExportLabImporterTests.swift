import Foundation
import XCTest
@testable import HealthBridgeCompanionCore

final class AppleHealthExportLabImporterTests: XCTestCase {
    private func observation(
        id: String = "3F9A9E10-0000-0000-0000-000000000001",
        code: [String: Any]? = [
            "coding": [["system": "http://loinc.org", "code": "2951-2"]],
            "text": "Sodium",
        ],
        category: Any? = ["coding": [["code": "laboratory"]]],
        effectiveDateTime: String? = "2026-06-04T08:00:00-05:00",
        valueQuantity: [String: Any]? = ["value": 140.0, "unit": "mmol/L"],
        valueString: String? = nil,
        referenceRange: [[String: Any]]? = [["low": ["value": 136.0], "high": ["value": 145.0]]],
        component: [[String: Any]]? = nil
    ) -> [String: Any] {
        var json: [String: Any] = ["resourceType": "Observation", "id": id]
        if let code { json["code"] = code }
        if let category { json["category"] = category }
        if let effectiveDateTime { json["effectiveDateTime"] = effectiveDateTime }
        if let valueQuantity { json["valueQuantity"] = valueQuantity }
        if let valueString { json["valueString"] = valueString }
        if let referenceRange { json["referenceRange"] = referenceRange }
        if let component { json["component"] = component }
        return json
    }

    func testNumericObservationWithStructuredRange() throws {
        let result = try XCTUnwrap(
            AppleHealthExportLabImporter.labResult(fromObservation: observation())
        )

        XCTAssertEqual(result.loinc, "2951-2")
        XCTAssertEqual(result.name, "Sodium")
        XCTAssertEqual(result.category, "laboratory")
        XCTAssertEqual(result.effectiveDate, "2026-06-04")
        XCTAssertEqual(result.valueNum, 140.0)
        XCTAssertEqual(result.unit, "mmol/L")
        XCTAssertNil(result.valueText)
        XCTAssertEqual(result.refLow, 136.0)
        XCTAssertEqual(result.refHigh, 145.0)
        XCTAssertEqual(result.sourceKey, "apple_health.export")
    }

    func testClientRecordIDIsStableAndMatchesPattern() throws {
        let a = AppleHealthExportLabImporter.clientRecordID(forObservationID: "same-id")
        let b = AppleHealthExportLabImporter.clientRecordID(forObservationID: "same-id")
        let c = AppleHealthExportLabImporter.clientRecordID(forObservationID: "different-id")

        XCTAssertEqual(a, b)
        XCTAssertNotEqual(a, c)
        XCTAssertTrue(a.hasPrefix("hk-labobs-"))
        let suffix = a.dropFirst("hk-labobs-".count)
        XCTAssertEqual(suffix.count, 16)
        XCTAssertTrue(suffix.allSatisfy { $0.isHexDigit && $0.isLowercase || $0.isNumber })
    }

    func testTextOnlyObservationFallsBackToValueText() throws {
        let json = observation(valueQuantity: nil, valueString: "Negative", referenceRange: nil)

        let result = try XCTUnwrap(AppleHealthExportLabImporter.labResult(fromObservation: json))

        XCTAssertEqual(result.valueText, "Negative")
        XCTAssertNil(result.valueNum)
    }

    func testFreeTextReferenceRangeIsParsed() throws {
        let json = observation(referenceRange: [["text": "10-20"]])

        let result = try XCTUnwrap(AppleHealthExportLabImporter.labResult(fromObservation: json))

        XCTAssertEqual(result.refLow, 10)
        XCTAssertEqual(result.refHigh, 20)
        XCTAssertEqual(result.refText, "10-20")
    }

    func testLessThanReferenceRangeIsParsedAsUpperBoundOnly() throws {
        let json = observation(referenceRange: [["text": "<5"]])

        let result = try XCTUnwrap(AppleHealthExportLabImporter.labResult(fromObservation: json))

        XCTAssertNil(result.refLow)
        XCTAssertEqual(result.refHigh, 5)
    }

    func testMultiComponentObservationBorrowsFirstLoincCodedComponent() throws {
        let json = observation(
            code: ["coding": [], "text": "Blood Pressure"],
            valueQuantity: nil,
            component: [
                [
                    "code": ["coding": [["system": "http://loinc.org", "code": "8480-6"]]],
                    "valueQuantity": ["value": 118, "unit": "mmHg"],
                ],
                [
                    "code": ["coding": [["system": "http://loinc.org", "code": "8462-4"]]],
                    "valueQuantity": ["value": 76, "unit": "mmHg"],
                ],
            ]
        )

        let result = try XCTUnwrap(AppleHealthExportLabImporter.labResult(fromObservation: json))

        XCTAssertEqual(result.valueNum, 118)
        XCTAssertEqual(result.unit, "mmHg")
    }

    func testMissingLoincFallsBackToTextThenGenericName() throws {
        let noLoinc = observation(code: ["coding": [], "text": ""])

        let result = try XCTUnwrap(AppleHealthExportLabImporter.labResult(fromObservation: noLoinc))

        XCTAssertNil(result.loinc)
        XCTAssertEqual(result.name, "Lab Result")
    }

    func testMissingIdIsRejected() {
        var json = observation()
        json.removeValue(forKey: "id")

        XCTAssertNil(AppleHealthExportLabImporter.labResult(fromObservation: json))
    }

    func testMissingEffectiveDateIsRejected() {
        let json = observation(effectiveDateTime: nil)

        XCTAssertNil(AppleHealthExportLabImporter.labResult(fromObservation: json))
    }

    func testEffectivePeriodStartIsUsedWhenDateTimeAbsent() throws {
        var json = observation(effectiveDateTime: nil)
        json["effectivePeriod"] = ["start": "2026-05-01T00:00:00Z"]

        let result = try XCTUnwrap(AppleHealthExportLabImporter.labResult(fromObservation: json))

        XCTAssertEqual(result.effectiveDate, "2026-05-01")
    }

    // MARK: - End-to-end zip read (proves the ZIPFoundation usage compiles and works)

    func testImportLabResultsReadsOnlyClinicalRecordsJSONFromZip() throws {
        let tempDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: tempDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempDirectory) }

        let zipURL = tempDirectory.appendingPathComponent("export.zip")
        let archive = try XCTUnwrap(Archive(url: zipURL, accessMode: .create))

        let observationData = try JSONSerialization.data(withJSONObject: observation())
        try archive.addEntry(
            with: "clinical-records/Observation-1.json",
            type: .file,
            uncompressedSize: Int64(observationData.count),
            provider: { position, size -> Data in
                observationData.subdata(in: Int(position)..<Int(position) + size)
            }
        )
        let irrelevantData = Data("not clinical data".utf8)
        try archive.addEntry(
            with: "workout-routes/route.gpx",
            type: .file,
            uncompressedSize: Int64(irrelevantData.count),
            provider: { position, size -> Data in
                irrelevantData.subdata(in: Int(position)..<Int(position) + size)
            }
        )

        let summary = try AppleHealthExportLabImporter.importLabResults(fromZipAt: zipURL)

        XCTAssertEqual(summary.observationCount, 1)
        XCTAssertEqual(summary.skippedCount, 0)
        XCTAssertEqual(summary.labResults.count, 1)
        XCTAssertEqual(summary.labResults[0].loinc, "2951-2")
    }
}
