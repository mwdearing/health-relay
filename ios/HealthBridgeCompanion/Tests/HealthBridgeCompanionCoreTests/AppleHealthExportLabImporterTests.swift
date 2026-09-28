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

    // MARK: - End-to-end zip read (MinimalZipReader, real zip bytes)

    /// A real zip (built with Python's stdlib `zipfile`, not this codebase, so it can't
    /// share a bug with the reader under test) containing three entries, nested under a
    /// top-level `apple_health_export/` folder exactly as Apple's real export does: a
    /// DEFLATEd clinical-records Observation, an irrelevant DEFLATEd non-clinical file,
    /// and a STORED (uncompressed) clinical-records Observation. The nesting is what a
    /// real device caught (2026-09-28): the importer's original path filter matched only
    /// a bare `clinical-records/` prefix, which a real export's paths never have.
    private static let syntheticExportZipBase64 = """
    UEsDBBQAAAAIAKyUPF0yAMxf/wAAAJ4BAAA3AAAAYXBwbGVfaGVhbHRoX2V4cG9ydC9jbGluaWNhbC1yZWNvcmRzL09ic2VydmF0aW9uLTEuanNvbl2PT2uEMBDFv4rM2T+ju0rNbaHtqVDaeisesjpqQJMljray+N1LtLTs5hDCm/d7b3IFS6OZbEXFciEQHryeR7KzZGU0+B6o2omH5/yUP8UYIOL99Xti5x5Z8jQ6olFa9k6qJFNr7ALC+7xCZWql27/31tjLs7GSnWct19Ix++TWPi4j0+CAjvkioqg3SlehsS38I5DkaRwksMUwfbPTPkytpgFW3wNqGqpYzfQomQo17AwmWYBZgMcCHwSicD9LBaILnmU/0dskNStetqU2BYQXHzFE34NJq61mGEwfvWw1lhqypCt6l7qlff/efN3ihyxEZ+5U290FpyGua7n+AFBLAwQUAAAACACslDxdRGMyxhMAAAARAAAALAAAAGFwcGxlX2hlYWx0aF9leHBvcnQvd29ya291dC1yb3V0ZXMvcm91dGUuZ3B4y8svUUjOyczLTE7MUUhJLEkEAFBLAwQUAAAAAAAAACEAUuL19J4BAACeAQAANwAAAGFwcGxlX2hlYWx0aF9leHBvcnQvY2xpbmljYWwtcmVjb3Jkcy9PYnNlcnZhdGlvbi0yLmpzb257InJlc291cmNlVHlwZSI6ICJPYnNlcnZhdGlvbiIsICJpZCI6ICJBQUFBQUFBQS0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDIiLCAic3RhdHVzIjogImZpbmFsIiwgImNhdGVnb3J5IjogW3siY29kaW5nIjogW3siY29kZSI6ICJsYWJvcmF0b3J5In1dfV0sICJjb2RlIjogeyJjb2RpbmciOiBbeyJzeXN0ZW0iOiAiaHR0cDovL2xvaW5jLm9yZyIsICJjb2RlIjogIjI5NTEtMiJ9XSwgInRleHQiOiAiU29kaXVtIn0sICJlZmZlY3RpdmVEYXRlVGltZSI6ICIyMDI2LTA2LTA1VDA4OjAwOjAwLTA1OjAwIiwgInZhbHVlUXVhbnRpdHkiOiB7InZhbHVlIjogMTQwLjAsICJ1bml0IjogIm1tb2wvTCJ9LCAicmVmZXJlbmNlUmFuZ2UiOiBbeyJsb3ciOiB7InZhbHVlIjogMTM2LjB9LCAiaGlnaCI6IHsidmFsdWUiOiAxNDUuMH19XX1QSwECFAMUAAAACACslDxdMgDMX/8AAACeAQAANwAAAAAAAAAAAAAAgAEAAAAAYXBwbGVfaGVhbHRoX2V4cG9ydC9jbGluaWNhbC1yZWNvcmRzL09ic2VydmF0aW9uLTEuanNvblBLAQIUAxQAAAAIAKyUPF1EYzLGEwAAABEAAAAsAAAAAAAAAAAAAACAAVQBAABhcHBsZV9oZWFsdGhfZXhwb3J0L3dvcmtvdXQtcm91dGVzL3JvdXRlLmdweFBLAQIUAxQAAAAAAAAAIQBS4vX0ngEAAJ4BAAA3AAAAAAAAAAAAAACAAbEBAABhcHBsZV9oZWFsdGhfZXhwb3J0L2NsaW5pY2FsLXJlY29yZHMvT2JzZXJ2YXRpb24tMi5qc29uUEsFBgAAAAADAAMAJAEAAKQDAAAAAA==
    """

    private func writeSyntheticExportZip() throws -> URL {
        let tempDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: tempDirectory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: tempDirectory) }

        let data = try XCTUnwrap(Data(base64Encoded: Self.syntheticExportZipBase64))
        let zipURL = tempDirectory.appendingPathComponent("export.zip")
        try data.write(to: zipURL)
        return zipURL
    }

    func testImportLabResultsReadsOnlyClinicalRecordsJSONFromZip() throws {
        let zipURL = try writeSyntheticExportZip()

        let summary = try AppleHealthExportLabImporter.importLabResults(fromZipAt: zipURL)

        // Two clinical-records Observations (one DEFLATEd, one STORED), nested under
        // apple_health_export/ exactly like a real export; the workout-routes/route.gpx
        // entry is neither clinical-records nor .json and must be skipped without even
        // attempting to parse it as JSON.
        XCTAssertEqual(summary.observationCount, 2)
        XCTAssertEqual(summary.skippedCount, 0)
        XCTAssertEqual(summary.labResults.count, 2)
        XCTAssertEqual(Set(summary.labResults.map(\.effectiveDate)), ["2026-06-04", "2026-06-05"])
        XCTAssertTrue(summary.labResults.allSatisfy { $0.loinc == "2951-2" && $0.valueNum == 140.0 })
    }

    func testMinimalZipReaderListsEntriesWithCorrectSizesAndMethods() throws {
        let zipURL = try writeSyntheticExportZip()

        let entries = try MinimalZipReader.listEntries(at: zipURL)

        let paths = Set(entries.map(\.path))
        XCTAssertEqual(paths, [
            "apple_health_export/clinical-records/Observation-1.json",
            "apple_health_export/workout-routes/route.gpx",
            "apple_health_export/clinical-records/Observation-2.json",
        ])
        let deflated = try XCTUnwrap(entries.first { $0.path == "apple_health_export/clinical-records/Observation-1.json" })
        XCTAssertEqual(deflated.compressionMethod, 8)
        let stored = try XCTUnwrap(entries.first { $0.path == "apple_health_export/clinical-records/Observation-2.json" })
        XCTAssertEqual(stored.compressionMethod, 0)
        XCTAssertEqual(stored.compressedSize, stored.uncompressedSize)
    }
}
