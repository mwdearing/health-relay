import Foundation

/// Builds a one-shot lab-results batch from an `AppleHealthExportLabImporter.Summary`
/// (HealthRelay addition). Unlike the HealthKit-backed lanes, there is no cursor here --
/// this is a manual, user-initiated file import, not a recurring background sync -- and
/// dedup on re-import is `client_record_id` uniqueness on the receiver, not a cursor.
public enum LabResultImportBatchFactory {
    public static func makeLabResultBatch(
        results: [HealthBridgeLabResult],
        generatedAt: Date = Date()
    ) -> HealthBridgeBatchV1 {
        let source = HealthBridgeSource(
            sourceKey: AppleHealthExportLabImporter.sourceKey,
            name: "Apple Health export.zip import",
            kind: .manual,
            bundleID: HealthBridgeAppIdentity.bundleIdentifier,
            deviceModel: "iPhone"
        )
        let generatedAtString = HealthBridgeUTCFormatter.string(from: generatedAt)
        let window = HealthBridgeTimeWindow(startTime: generatedAtString, endTime: generatedAtString)

        return HealthBridgeBatchV1(
            generatedAt: generatedAtString,
            exportWindow: window,
            sources: [source],
            healthTypes: [.labResult],
            samples: [],
            workouts: [],
            labResults: results,
            sleepSessions: [],
            deletedRecords: [],
            sync: HealthBridgeSyncContext(syncWindow: window, cursors: [])
        )
    }
}
