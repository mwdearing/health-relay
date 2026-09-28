import Foundation

/// Builds a foreground medication-dose-event batch (HealthRelay addition).
public enum MedicationDoseEventSyncBatchFactory {
    public static let foregroundCursorKind = "foreground_medication_dose_event_sync"

    public static func makeMedicationDoseEventBatch(
        events: [HealthKitMedicationDoseEventSummary],
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
            healthTypes: [.medicationDoseEvents],
            samples: [],
            workouts: [],
            medicationDoseEvents: medicationDoseEventRecords(from: events, source: source),
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

    static func medicationDoseEventRecords(
        from events: [HealthKitMedicationDoseEventSummary],
        source: HealthBridgeSource
    ) -> [HealthBridgeMedicationDoseEvent] {
        events
            .sorted { lhs, rhs in
                if lhs.start == rhs.start {
                    return lhs.uuid.uuidString < rhs.uuid.uuidString
                }
                return lhs.start < rhs.start
            }
            .map { event in
                HealthBridgeMedicationDoseEvent(
                    clientRecordID: clientRecordID(for: event.uuid),
                    sourceKey: source.sourceKey,
                    medicationName: event.medicationName.isEmpty ? "unnamed medication" : event.medicationName,
                    medicationConceptKey: event.conceptKey,
                    status: event.status,
                    statusRaw: event.statusRaw,
                    startTime: HealthBridgeUTCFormatter.string(from: event.start),
                    scheduledTime: event.scheduled.map { HealthBridgeUTCFormatter.string(from: $0) },
                    dose: event.dose.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil },
                    unit: event.unit.flatMap { $0.isEmpty ? nil : $0 }
                )
            }
    }

    static func clientRecordID(for uuid: UUID) -> String {
        "hk-meddose-\(uuid.uuidString.lowercased())"
    }
}
