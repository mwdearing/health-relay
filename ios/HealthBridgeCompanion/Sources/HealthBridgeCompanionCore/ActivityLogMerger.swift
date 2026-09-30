import Foundation

public enum ActivityLogLaneUpdate: Equatable, Sendable {
    case started(String)
    case finished
}

/// One displayed activity-log line with an identity that survives list changes.
public struct ActivityLogRow: Equatable, Identifiable, Sendable {
    public let id: String
    public let text: String
}

public enum ActivityLogMerger {
    public static let entryLimit = 30
    private static let separator = " — "
    private static let progressLabel = "Sync in progress"
    private static let emptyLabel = "No new data to send"
    private static let countMarker = " (×"

    private static let laneNames: [String: String] = [
        "steps": "Steps",
        "dailyActivity": "Daily activity",
        "workouts": "Workouts",
        "sleep": "Sleep",
        "electrocardiograms": "ECG",
        "medicationDoseEvents": "Medications",
        "supportedQuantities": "Other metrics",
    ]

    public static func laneUpdate(fromManualMessage message: String) -> ActivityLogLaneUpdate? {
        if message == "[Manual] all lanes finished" { return .finished }
        let prefix = "[Manual] lane "
        guard message.hasPrefix(prefix) else { return nil }
        let raw = String(message.dropFirst(prefix.count))
        return laneNames[raw].map { .started($0) }
    }

    public static func appending(
        label: String,
        lane: String?,
        time: String,
        to entries: [String],
        limit: Int = entryLimit
    ) -> [String] {
        var entries = entries
        let display = lane.map { "\($0): \(label)" } ?? label
        let lastText = entries.last.map(text(of:))

        if label == emptyLabel {
            if let lane, lastText == "\(lane): \(progressLabel)" {
                entries.removeLast()
            }
            if let last = entries.last {
                let base = baseText(of: text(of: last))
                if base == display {
                    let count = repeatCount(of: text(of: last)) + 1
                    entries[entries.count - 1] = "\(time)\(separator)\(display)\(countMarker)\(count))"
                    return trimmed(entries, limit: limit)
                }
            }
        } else if lastText == display {
            return entries
        }

        entries.append("\(time)\(separator)\(display)")
        return trimmed(entries, limit: limit)
    }

    private static func text(of entry: String) -> String {
        guard let range = entry.range(of: separator) else { return entry }
        return String(entry[range.upperBound...])
    }

    private static func baseText(of text: String) -> String {
        guard let range = text.range(of: countMarker, options: .backwards), text.hasSuffix(")") else { return text }
        return String(text[..<range.lowerBound])
    }

    private static func repeatCount(of text: String) -> Int {
        guard let range = text.range(of: countMarker, options: .backwards), text.hasSuffix(")") else { return 1 }
        let digits = text[range.upperBound...].dropLast()
        return Int(digits) ?? 1
    }

    private static func trimmed(_ entries: [String], limit: Int) -> [String] {
        entries.count > limit ? Array(entries.suffix(limit)) : entries
    }

    /// Rows for display. The identity is the entry text plus how many identical entries
    /// precede it, so it does not depend on list position: trimming the oldest entries or
    /// collapsing the newest one never re-identifies the rows in between, and repeated
    /// identical lines still get distinct ids.
    public static func identifiedRows(from entries: [String]) -> [ActivityLogRow] {
        var seen: [String: Int] = [:]
        return entries.map { entry in
            let occurrence = seen[entry, default: 0]
            seen[entry] = occurrence + 1
            return ActivityLogRow(id: "\(occurrence)|\(entry)", text: entry)
        }
    }

    /// Plain text for Share or Copy All: exactly the rows the log shows, oldest first, one per
    /// line. A row that carries a setup link or key material is left out instead of exported.
    public static func exportText(from entries: [String]) -> String {
        entries
            .filter { !containsPairingMaterial($0) }
            .joined(separator: "\n")
    }

    private static let pairingMarkers = [
        "healthrelay://", "healthbridge://", "payload=", "hbi_", "hb_pairing", "bearer ",
    ]

    private static func containsPairingMaterial(_ entry: String) -> Bool {
        let lowered = entry.lowercased()
        return pairingMarkers.contains { lowered.contains($0) }
    }
}
