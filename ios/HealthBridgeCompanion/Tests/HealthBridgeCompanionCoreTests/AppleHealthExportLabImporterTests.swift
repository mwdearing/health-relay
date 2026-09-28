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
    /// share a bug with the reader under test) containing three entries: a DEFLATEd
    /// clinical-records Observation, an irrelevant DEFLATEd non-clinical file, and a
    /// STORED (uncompressed) clinical-records Observation -- exercising both ZIP
    /// compression methods `MinimalZipReader` supports.
    private static let syntheticExportZipBase64 = """
    UEsDBBQAAAAIAAuKPF0yAMxf/wAAAJ4BAAAjAAAAY2xpbmljYWwtcmVjb3Jkcy9PYnNlcnZhdGlvbi0xLmpzb25dj09rhDAQxb+KzNk/o7tKzW2h7alQ2norHrI6akCTJY62svjdS7S07OYQwpv3e29yBUujmWxFxXIhEB68nkeys2RlNPgeqNqJh+f8lD/FGCDi/fV7YuceWfI0OqJRWvZOqiRTa+wCwvu8QmVqpdu/99bYy7Oxkp1nLdfSMfvk1j4uI9PggI75IqKoN0pXobEt/COQ5GkcJLDFMH2z0z5MraYBVt8DahqqWM30KJkKNewMJlmAWYDHAh8EonA/SwWiC55lP9HbJDUrXralNgWEFx8xRN+DSautZhhMH71sNZYasqQrepe6pX3/3nzd4ocsRGfuVNvdBachrmu5/gBQSwMEFAAAAAgAC4o8XURjMsYTAAAAEQAAABgAAAB3b3Jrb3V0LXJvdXRlcy9yb3V0ZS5ncHjLyy9RSM7JzMtMTsxRSEksSQQAUEsDBBQAAAAAAAAAIQBS4vX0ngEAAJ4BAAAjAAAAY2xpbmljYWwtcmVjb3Jkcy9PYnNlcnZhdGlvbi0yLmpzb257InJlc291cmNlVHlwZSI6ICJPYnNlcnZhdGlvbiIsICJpZCI6ICJBQUFBQUFBQS0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDIiLCAic3RhdHVzIjogImZpbmFsIiwgImNhdGVnb3J5IjogW3siY29kaW5nIjogW3siY29kZSI6ICJsYWJvcmF0b3J5In1dfV0sICJjb2RlIjogeyJjb2RpbmciOiBbeyJzeXN0ZW0iOiAiaHR0cDovL2xvaW5jLm9yZyIsICJjb2RlIjogIjI5NTEtMiJ9XSwgInRleHQiOiAiU29kaXVtIn0sICJlZmZlY3RpdmVEYXRlVGltZSI6ICIyMDI2LTA2LTA1VDA4OjAwOjAwLTA1OjAwIiwgInZhbHVlUXVhbnRpdHkiOiB7InZhbHVlIjogMTQwLjAsICJ1bml0IjogIm1tb2wvTCJ9LCAicmVmZXJlbmNlUmFuZ2UiOiBbeyJsb3ciOiB7InZhbHVlIjogMTM2LjB9LCAiaGlnaCI6IHsidmFsdWUiOiAxNDUuMH19XX1QSwECFAMUAAAACAALijxdMgDMX/8AAACeAQAAIwAAAAAAAAAAAAAAgAEAAAAAY2xpbmljYWwtcmVjb3Jkcy9PYnNlcnZhdGlvbi0xLmpzb25QSwECFAMUAAAACAALijxdRGMyxhMAAAARAAAAGAAAAAAAAAAAAAAAgAFAAQAAd29ya291dC1yb3V0ZXMvcm91dGUuZ3B4UEsBAhQDFAAAAAAAAAAhAFLi9fSeAQAAngEAACMAAAAAAAAAAAAAAIABiQEAAGNsaW5pY2FsLXJlY29yZHMvT2JzZXJ2YXRpb24tMi5qc29uUEsFBgAAAAADAAMA6AAAAGgDAAAAAA==
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

        // Two clinical-records Observations (one DEFLATEd, one STORED); the
        // workout-routes/route.gpx entry is neither clinical-records nor .json and must
        // be skipped without even attempting to parse it as JSON.
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
            "clinical-records/Observation-1.json",
            "workout-routes/route.gpx",
            "clinical-records/Observation-2.json",
        ])
        let deflated = try XCTUnwrap(entries.first { $0.path == "clinical-records/Observation-1.json" })
        XCTAssertEqual(deflated.compressionMethod, 8)
        let stored = try XCTUnwrap(entries.first { $0.path == "clinical-records/Observation-2.json" })
        XCTAssertEqual(stored.compressionMethod, 0)
        XCTAssertEqual(stored.compressedSize, stored.uncompressedSize)
    }
}
