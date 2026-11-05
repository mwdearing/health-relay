import Foundation

/// Builds a foreground ECG batch (HealthRelay addition). Mirrors `WorkoutSyncBatchFactory`.
public enum ElectrocardiogramSyncBatchFactory {
    public static let foregroundCursorKind = "foreground_electrocardiogram_sync"

    public static func makeElectrocardiogramBatch(
        recordings: [HealthKitElectrocardiogramSummary],
        windowStart: Date,
        windowEnd: Date,
        generatedAt: Date = Date()
    ) -> HealthBridgeBatchV1 {
        let source = HealthBridgeAppleHealthSource.phone
        let window = HealthBridgeTimeWindow(
            startTime: HealthBridgeUTCFormatter.string(from: windowStart),
            endTime: HealthBridgeUTCFormatter.string(from: windowEnd)
        )

        return HealthBridgeBatchV1(
            generatedAt: HealthBridgeUTCFormatter.string(from: generatedAt),
            exportWindow: window,
            sources: [source],
            healthTypes: [.electrocardiogram],
            samples: [],
            workouts: [],
            electrocardiograms: electrocardiogramRecords(from: recordings, source: source),
            sleepSessions: [],
            deletedRecords: [],
            sync: HealthBridgeSyncContext(
                syncWindow: window,
                cursors: [
                    HealthBridgeSyncCursor(
                        sourceKey: source.sourceKey,
                        cursorKind: foregroundCursorKind,
                        cursorValue: HealthBridgeUTCFormatter.string(from: windowEnd)
                    )
                ]
            )
        )
    }

    /// Cursor kind that stores the HealthKit anchor of the ECG deletion lane.
    public static let deletionAnchorCursorKind = "foreground_electrocardiogram_anchor"
    public static let deletedRecordFamily = "electrocardiogram"

    /// A deletion-only batch: tombstones for recordings the user deleted from Health, plus the
    /// new anchor as its cursor so the outbox applies it only after the receiver accepts the batch.
    public static func makeElectrocardiogramDeletionBatch(
        deletedUUIDs: [UUID],
        anchorCursorValue: String,
        windowStart: Date,
        windowEnd: Date,
        generatedAt: Date = Date()
    ) -> HealthBridgeBatchV1 {
        let source = HealthBridgeAppleHealthSource.phone
        let window = HealthBridgeTimeWindow(
            startTime: HealthBridgeUTCFormatter.string(from: windowStart),
            endTime: HealthBridgeUTCFormatter.string(from: windowEnd)
        )
        let deletedAt = HealthBridgeUTCFormatter.string(from: windowEnd)
        let deleted = Array(Set(deletedUUIDs))
            .sorted { $0.uuidString < $1.uuidString }
            .map { uuid in
                HealthBridgeDeletedRecord(
                    recordFamily: deletedRecordFamily,
                    sourceKey: source.sourceKey,
                    clientRecordID: clientRecordID(for: uuid),
                    deletedAt: deletedAt
                )
            }
        return HealthBridgeBatchV1(
            generatedAt: HealthBridgeUTCFormatter.string(from: generatedAt),
            exportWindow: window,
            sources: [source],
            healthTypes: [.electrocardiogram],
            samples: [],
            workouts: [],
            electrocardiograms: [],
            sleepSessions: [],
            deletedRecords: deleted,
            sync: HealthBridgeSyncContext(
                syncWindow: window,
                cursors: [
                    HealthBridgeSyncCursor(
                        sourceKey: source.sourceKey,
                        cursorKind: deletionAnchorCursorKind,
                        cursorValue: anchorCursorValue
                    )
                ]
            )
        )
    }

    static func electrocardiogramRecords(
        from recordings: [HealthKitElectrocardiogramSummary],
        source: HealthBridgeSource
    ) -> [HealthBridgeElectrocardiogram] {
        recordings
            .filter { $0.start < $0.end }
            .sorted { lhs, rhs in
                if lhs.start == rhs.start {
                    return lhs.uuid.uuidString < rhs.uuid.uuidString
                }
                return lhs.start < rhs.start
            }
            .map { recording in
                // The receiver requires the voltage array length to equal voltage_count;
                // a series with a gap is sent summary-only rather than mismatched.
                let voltages = recording.voltagesMicrovolts.flatMap { series -> [Double]? in
                    guard series.count == recording.voltageCount, series.allSatisfy(\.isFinite) else {
                        return nil
                    }
                    return series
                }
                return HealthBridgeElectrocardiogram(
                    clientRecordID: clientRecordID(for: recording.uuid),
                    sourceKey: source.sourceKey,
                    startTime: HealthBridgeUTCFormatter.string(from: recording.start),
                    endTime: HealthBridgeUTCFormatter.string(from: recording.end),
                    classification: recording.classification,
                    symptomsStatus: recording.symptomsStatus,
                    averageHeartRateBPM: recording.averageHeartRateBPM,
                    samplingFrequencyHz: recording.samplingFrequencyHz,
                    voltageCount: max(0, recording.voltageCount),
                    voltagesMicrovolts: voltages
                )
            }
    }

    static func clientRecordID(for uuid: UUID) -> String {
        "hk-ecg-\(uuid.uuidString.lowercased())"
    }
}

/// Rules for the foreground ECG deletion lane (HealthRelay addition).
public enum ElectrocardiogramLanePolicy {
    /// Uploads when something was deleted, or once to store the first anchor; later anchor
    /// moves without deletions are not worth an upload.
    public static func shouldSendDeletionBatch(deletedUUIDs: [UUID], hadStoredAnchor: Bool) -> Bool {
        !deletedUUIDs.isEmpty || !hadStoredAnchor
    }
}
