import Foundation

/// Fixed, invented data for the App Review sample mode.
///
/// Every value is synthetic. Nothing here is copied from a real export, receiver, medication
/// record or database. Literal text starts with "Sample", identifiers are zero-filled, and dates
/// are fixed in January 2026. The sample path never reads Apple Health and never contacts a receiver.
public enum SampleReviewLane: String, CaseIterable, Equatable, Sendable {
    case steps
    case sleep
    case ecg
    case medication
    case labs
}

public struct SampleReviewRecord: Equatable, Identifiable, Sendable {
    public let id: String
    public let lane: SampleReviewLane
    public let title: String
    public let quantity: Double
    public let startISO: String
    public let endISO: String
}

/// Screens the sample mode can open directly. `overview` is the default.
/// Screenshot scenes: overview, sync, ecg, labs. A consent scene is not part of this branch.
public enum SampleReviewScene: String, CaseIterable, Equatable, Sendable {
    case overview
    case sync
    case ecg
    case labs
}

/// Launch-argument selection for App Review and screenshots.
///
/// Launching with `-HealthRelaySampleScene <name>` opens the app in sample mode at that scene.
/// A missing or unknown name opens `overview`. Without the flag this returns nil and the live app runs.
public enum SampleReviewLaunch {
    public static let argumentName = "-HealthRelaySampleScene"

    public static func requestedScene(arguments: [String]) -> SampleReviewScene? {
        guard let index = arguments.firstIndex(of: argumentName) else { return nil }
        let name = arguments.indices.contains(index + 1) ? arguments[index + 1] : ""
        return SampleReviewScene(rawValue: name) ?? .overview
    }
}

/// Source rows on the overview. Titles and units live in the view; only quantities are here.
public enum SampleSourceKind: CaseIterable, Equatable, Sendable {
    case steps
    case sleep
    case medication
}

public enum SampleReviewData {
    public static let receiverName = "Sample Receiver"
    public static let receiverHost = "sample.invalid"
    public static let lastSyncedLabel = "Sample 9:00 AM"
    public static let pendingLabImportCount = 3

    public static func sourceQuantity(_ kind: SampleSourceKind) -> Double {
        switch kind {
        case .steps: 1
        case .sleep: 1
        case .medication: 2
        }
    }

    public static let syncRunLog: [String] = [
        "Sample sync started.",
        "Sample steps: 1 record staged.",
        "Sample sleep: 1 session staged.",
        "Sample ECG: 1 recording staged.",
        "Sample medication: 2 dose events staged.",
        "Sample sync finished. Nothing left to send.",
    ]

    public static let records: [SampleReviewRecord] = [
        SampleReviewRecord(
            id: "00000000-0000-0000-0000-000000000001",
            lane: .steps,
            title: "Sample Step Count",
            quantity: 4200,
            startISO: "2026-01-10T08:00:00Z",
            endISO: "2026-01-10T08:30:00Z"
        ),
        SampleReviewRecord(
            id: "00000000-0000-0000-0000-000000000002",
            lane: .sleep,
            title: "Sample Sleep Session",
            quantity: 7.5,
            startISO: "2026-01-11T23:00:00Z",
            endISO: "2026-01-12T06:30:00Z"
        ),
        SampleReviewRecord(
            id: "00000000-0000-0000-0000-000000000003",
            lane: .ecg,
            title: "Sample ECG Recording",
            quantity: 30,
            startISO: "2026-01-14T10:00:00Z",
            endISO: "2026-01-14T10:00:30Z"
        ),
        SampleReviewRecord(
            id: "00000000-0000-0000-0000-000000000004",
            lane: .medication,
            title: "Sample Medication A",
            quantity: 1.0,
            startISO: "2026-01-12T08:00:00Z",
            endISO: "2026-01-12T08:00:00Z"
        ),
        SampleReviewRecord(
            id: "00000000-0000-0000-0000-000000000005",
            lane: .medication,
            title: "Sample Medication B",
            quantity: 1.0,
            startISO: "2026-01-13T20:00:00Z",
            endISO: "2026-01-13T20:00:00Z"
        ),
        SampleReviewRecord(
            id: "00000000-0000-0000-0000-000000000006",
            lane: .labs,
            title: "Sample Lab Result A",
            quantity: 1.0,
            startISO: "2026-01-15T00:00:00Z",
            endISO: "2026-01-15T00:00:00Z"
        ),
    ]

    public static func records(for lane: SampleReviewLane) -> [SampleReviewRecord] {
        records.filter { $0.lane == lane }
    }
}
