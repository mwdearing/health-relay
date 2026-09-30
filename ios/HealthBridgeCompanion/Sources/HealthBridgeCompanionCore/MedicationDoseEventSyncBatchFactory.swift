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

/// Rules shared by the foreground and the background medication dose lane (HealthRelay addition).
///
/// HealthKit never reveals whether read access was granted (`authorizationStatus(for:)` reports
/// write status only) and per-object authorization always shows UI. So the background lane never
/// calls `requestPerObjectReadAuthorization`: it just queries, and samples the user has not shared
/// simply do not appear. Access is granted once by the manual Sync Now lane.
///
/// Because "no access" and "no new doses" look identical, the background lane never uploads a
/// cursor-only batch: an empty read must not advance the cursor shared with the foreground lane,
/// or doses shared later could fall outside the replay window.
public enum MedicationDoseLanePolicy {
    public static let cursorKind = MedicationDoseEventSyncBatchFactory.foregroundCursorKind
    public static let replayOverlapDays = 3

    public static func requestsPerObjectAuthorization(for mode: HealthBridgeSyncExecutionMode) -> Bool {
        mode.shouldRequestReadAuthorization
    }

    /// Start of the read window. With a usable cursor both modes replay the last
    /// `replayOverlapDays` before it (the receiver dedups by record id). Without one, the
    /// foreground lane honours the history depth and the background lane reads one day.
    public static func windowStart(
        mode: HealthBridgeSyncExecutionMode,
        historyFallbackStart: Date,
        end: Date,
        cursorValue: String?,
        calendar: Calendar = Calendar(identifier: .gregorian)
    ) -> Date {
        let fallbackStart: Date
        if let days = mode.cursorlessFallbackDays {
            fallbackStart = end.addingTimeInterval(-TimeInterval(days * 24 * 60 * 60))
        } else {
            fallbackStart = historyFallbackStart
        }
        return ForegroundSyncWindowPolicy.windowStart(
            fallbackStart: fallbackStart,
            end: end,
            cursorValue: cursorValue,
            replayOverlapDays: replayOverlapDays,
            alignToStartOfDay: false,
            calendar: calendar
        )
    }

    /// The background lane only persists the shared cursor when it started from a usable one; the
    /// foreground lane establishes it.
    public static func shouldPersistCursor(
        mode: HealthBridgeSyncExecutionMode,
        cursorValue: String?,
        end: Date
    ) -> Bool {
        mode.shouldPersistSharedProgress(
            hadUsableCursor: ForegroundSyncWindowPolicy.hasUsableCursorValue(cursorValue, before: end)
        )
    }

    public static func shouldUpload(_ batch: HealthBridgeBatchV1, mode: HealthBridgeSyncExecutionMode) -> Bool {
        switch mode {
        case .foreground:
            return ForegroundSyncUploadPolicy.shouldUpload(batch)
        case .automatic:
            return !batch.medicationDoseEvents.isEmpty
        }
    }
}
