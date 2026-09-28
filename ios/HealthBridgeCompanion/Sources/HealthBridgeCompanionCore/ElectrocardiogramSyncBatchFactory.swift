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
