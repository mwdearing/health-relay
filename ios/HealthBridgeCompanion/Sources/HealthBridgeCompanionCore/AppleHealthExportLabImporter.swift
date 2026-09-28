import CryptoKit
import Foundation
import ZIPFoundation

/// HealthRelay addition: parses an Apple Health `export.zip` on-device to find lab
/// results. This is the export importer's only record family -- ECG and medications
/// already sync live via HealthKit and never need a manual export (see
/// `HealthBridgeLabResult`). Only `clinical-records/*.json` entries are read; nothing
/// else in the zip is opened, and nothing is ever written to disk -- each small member
/// is streamed straight into memory via ZIPFoundation's consumer closure, matching the
/// host-side `health_insights.labs` reference parser's field extraction.
public enum AppleHealthExportLabImporter {
    /// Distinct from the live HealthKit lanes' `apple_health.phone` so rows are
    /// traceable to a manual export rather than a live sync.
    public static let sourceKey = "apple_health.export"

    public enum ImportError: Error, Equatable {
        case cannotOpenArchive
    }

    public struct Summary: Equatable, Sendable {
        public let labResults: [HealthBridgeLabResult]
        /// Every zip member that parsed as a FHIR Observation, whether or not it
        /// produced a usable lab result (e.g. missing effective_date is skipped).
        public let observationCount: Int
        public let skippedCount: Int
    }

    public static func importLabResults(fromZipAt url: URL) throws -> Summary {
        guard let archive = try? Archive(url: url, accessMode: .read) else {
            throw ImportError.cannotOpenArchive
        }

        var labResults: [HealthBridgeLabResult] = []
        var observationCount = 0
        var skippedCount = 0

        for entry in archive {
            guard entry.type == .file,
                  entry.path.hasPrefix("clinical-records/"),
                  entry.path.hasSuffix(".json")
            else {
                continue
            }

            var data = Data()
            data.reserveCapacity(Int(entry.uncompressedSize))
            _ = try? archive.extract(entry) { chunk in
                data.append(chunk)
            }

            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  (json["resourceType"] as? String) == "Observation"
            else {
                continue
            }
            observationCount += 1

            if let result = labResult(fromObservation: json) {
                labResults.append(result)
            } else {
                skippedCount += 1
            }
        }

        return Summary(
            labResults: labResults,
            observationCount: observationCount,
            skippedCount: skippedCount
        )
    }

    // MARK: - Field extraction (mirrors health_insights.labs's parser)

    static func labResult(fromObservation json: [String: Any]) -> HealthBridgeLabResult? {
        guard let id = json["id"] as? String, !id.isEmpty else {
            return nil
        }
        guard let effectiveDate = effectiveDate(from: json) else {
            return nil
        }

        let loinc = loincCode(from: json)
        let name = displayName(from: json, loinc: loinc)
        let category = categoryCode(from: json)
        let (valueNum, unit, valueText) = valueFields(from: json)
        let (refLow, refHigh, refText) = referenceRange(from: json)

        return HealthBridgeLabResult(
            clientRecordID: clientRecordID(forObservationID: id),
            sourceKey: sourceKey,
            loinc: loinc,
            name: name,
            category: category,
            effectiveDate: effectiveDate,
            valueNum: valueNum,
            unit: unit,
            valueText: valueText,
            refLow: refLow,
            refHigh: refHigh,
            refText: refText
        )
    }

    static func clientRecordID(forObservationID id: String) -> String {
        let digest = SHA256.hash(data: Data(id.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
        return "hk-labobs-" + digest.prefix(16)
    }

    private static func codings(from codeField: Any?) -> [[String: Any]] {
        if let dict = codeField as? [String: Any] {
            return (dict["coding"] as? [[String: Any]]) ?? []
        }
        return []
    }

    private static func loincCode(from json: [String: Any]) -> String? {
        for coding in codings(from: json["code"]) {
            let system = (coding["system"] as? String) ?? ""
            if system.lowercased().contains("loinc"), let code = coding["code"] as? String {
                return code
            }
        }
        return nil
    }

    private static func displayName(from json: [String: Any], loinc: String?) -> String {
        if let code = json["code"] as? [String: Any],
           let text = code["text"] as? String,
           !text.isEmpty {
            return text
        }
        if let loinc {
            return "LOINC \(loinc)"
        }
        return "Lab Result"
    }

    private static func categoryCode(from json: [String: Any]) -> String? {
        let categoryEntries: [[String: Any]]
        if let dict = json["category"] as? [String: Any] {
            categoryEntries = [dict]
        } else if let list = json["category"] as? [[String: Any]] {
            categoryEntries = list
        } else {
            categoryEntries = []
        }
        for entry in categoryEntries {
            if let coding = (entry["coding"] as? [[String: Any]])?.first,
               let code = coding["code"] as? String {
                return code
            }
        }
        return nil
    }

    private static func effectiveDate(from json: [String: Any]) -> String? {
        if let value = json["effectiveDateTime"] as? String, value.count >= 10 {
            return String(value.prefix(10))
        }
        if let period = json["effectivePeriod"] as? [String: Any],
           let start = period["start"] as? String,
           start.count >= 10 {
            return String(start.prefix(10))
        }
        return nil
    }

    private static func numericQuantity(_ quantity: Any?) -> (Double, String?)? {
        guard let dict = quantity as? [String: Any] else { return nil }
        let value: Double?
        if let number = dict["value"] as? Double {
            value = number
        } else if let number = dict["value"] as? Int {
            value = Double(number)
        } else {
            value = nil
        }
        guard let value else { return nil }
        return (value, dict["unit"] as? String)
    }

    private static func valueFields(from json: [String: Any]) -> (Double?, String?, String?) {
        if let (value, unit) = numericQuantity(json["valueQuantity"]) {
            return (value, unit, nil)
        }
        if let text = json["valueString"] as? String, !text.isEmpty {
            if let numeric = Double(text) {
                return (numeric, nil, text)
            }
            return (nil, nil, text)
        }
        // Multi-component observations (e.g. blood pressure): borrow the first
        // LOINC-coded numeric component, matching the host-side parser.
        if let components = json["component"] as? [[String: Any]] {
            for component in components {
                guard !codings(from: component["code"]).isEmpty,
                      let (value, unit) = numericQuantity(component["valueQuantity"])
                else {
                    continue
                }
                return (value, unit, nil)
            }
        }
        return (nil, nil, nil)
    }

    private static func referenceRange(from json: [String: Any]) -> (Double?, Double?, String?) {
        guard let ranges = json["referenceRange"] as? [[String: Any]], let range = ranges.first else {
            return (nil, nil, nil)
        }
        let low = numericQuantity(range["low"])?.0
        let high = numericQuantity(range["high"])?.0
        let text = range["text"] as? String
        if low != nil || high != nil {
            return (low, high, text)
        }
        if let text, let parsed = parseFreeTextRange(text) {
            return (parsed.0, parsed.1, text)
        }
        return (nil, nil, text)
    }

    private static func parseFreeTextRange(_ text: String) -> (Double?, Double?)? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("<"), let value = Double(trimmed.dropFirst().trimmingCharacters(in: .whitespaces)) {
            return (nil, value)
        }
        if trimmed.hasPrefix(">"), let value = Double(trimmed.dropFirst().trimmingCharacters(in: .whitespaces)) {
            return (value, nil)
        }
        let parts = trimmed.split(separator: "-", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
        if parts.count == 2, let low = Double(parts[0]), let high = Double(parts[1]) {
            return (low, high)
        }
        return nil
    }
}
