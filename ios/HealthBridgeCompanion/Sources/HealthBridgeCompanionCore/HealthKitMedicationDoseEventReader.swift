import CryptoKit
import Foundation

#if canImport(HealthKit)
import HealthKit
#endif

/// One HealthKit medication dose event as plain values (HealthRelay addition).
public struct HealthKitMedicationDoseEventSummary: Equatable, Sendable {
    public let uuid: UUID
    public let medicationName: String
    public let conceptKey: String?
    public let status: HealthBridgeMedicationDoseEvent.Status
    public let statusRaw: Int
    public let start: Date
    public let scheduled: Date?
    public let dose: Double?
    public let unit: String?

    public init(
        uuid: UUID,
        medicationName: String,
        conceptKey: String?,
        status: HealthBridgeMedicationDoseEvent.Status,
        statusRaw: Int,
        start: Date,
        scheduled: Date?,
        dose: Double?,
        unit: String?
    ) {
        self.uuid = uuid
        self.medicationName = medicationName
        self.conceptKey = conceptKey
        self.status = status
        self.statusRaw = statusRaw
        self.start = start
        self.scheduled = scheduled
        self.dose = dose
        self.unit = unit
    }
}

public enum HealthKitMedicationDoseEventReaderError: Error, Equatable {
    case healthDataUnavailable
    case invalidWindow
    case perObjectAuthorizationDeclined
    case anchorUnavailable
    case nameLookupUnavailable
}

/// Dose events the user deleted or added since the stored anchor, plus the anchor to store once
/// they are delivered (RELAY-R2). A corrected log replaces the object, so the old uuid arrives as
/// a deletion and the replacement as an addition, which can start before the dose read window.
/// `deletedUUIDs` and `addedEvents` are empty when no anchor existed yet: the first read only
/// establishes the anchor (an anchorless read would return every dose ever recorded).
public struct HealthKitMedicationDoseEventChanges: Equatable, Sendable {
    public let deletedUUIDs: [UUID]
    public let addedEvents: [HealthKitMedicationDoseEventSummary]
    public let anchorCursorValue: String

    public init(
        deletedUUIDs: [UUID],
        addedEvents: [HealthKitMedicationDoseEventSummary] = [],
        anchorCursorValue: String
    ) {
        self.deletedUUIDs = deletedUUIDs
        self.addedEvents = addedEvents
        self.anchorCursorValue = anchorCursorValue
    }
}

/// Stable key for a HealthKit health concept: "c" + first 12 hex digits of the SHA-256 of the
/// archived identifier. The identifier has no public code and its description is a memory address,
/// but its secure-coded bytes are stable for the same concept across reads.
public enum HealthBridgeConceptKey {
    public static func make(archived data: Data) -> String {
        "c" + SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined().prefix(12)
    }
}

#if canImport(HealthKit)
@available(iOS 26.0, macOS 26.0, *)
public final class HealthKitMedicationDoseEventReader: @unchecked Sendable {
    private let healthStore: HKHealthStore

    public init(healthStore: HKHealthStore = HKHealthStore()) {
        self.healthStore = healthStore
    }

    /// Medication types are per-object authorized: they must go through
    /// `requestPerObjectReadAuthorization`, never `requestAuthorization` (which throws an
    /// uncatchable exception for them). Returns false when the user declined.
    public func requestPerObjectReadAuthorization() async throws -> Bool {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw HealthKitMedicationDoseEventReaderError.healthDataUnavailable
        }
        let types: [HKObjectType] = [
            HKObjectType.userAnnotatedMedicationType(),
            HKObjectType.medicationDoseEventType(),
        ]
        for type in types where type.requiresPerObjectAuthorization() {
            let granted: Bool = try await withCheckedThrowingContinuation { continuation in
                healthStore.requestPerObjectReadAuthorization(for: type, predicate: nil) { success, error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else {
                        continuation.resume(returning: success)
                    }
                }
            }
            if !granted {
                return false
            }
        }
        return true
    }

    /// Dose events in `[start, end)` sorted by start date, with the medication name joined from the
    /// user's medication list when the concept matches (nickname first, then display text).
    public func readMedicationDoseEvents(start: Date, end: Date) async throws -> [HealthKitMedicationDoseEventSummary] {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw HealthKitMedicationDoseEventReaderError.healthDataUnavailable
        }
        guard start < end else {
            throw HealthKitMedicationDoseEventReaderError.invalidWindow
        }

        let namesByConceptKey = try await medicationNamesByConceptKey()

        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: [.strictStartDate])
        let sortDescriptor = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)
        let events: [HKMedicationDoseEvent] = try await executeCancellableHealthKitQuery(healthStore: healthStore) { completion in
            HKSampleQuery(
                sampleType: HKObjectType.medicationDoseEventType(),
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: [sortDescriptor]
            ) { _, samples, error in
                if let error {
                    completion(.failure(error))
                    return
                }
                completion(.success((samples as? [HKMedicationDoseEvent]) ?? []))
            }
        }

        return events.map { Self.summary(for: $0, namesByConceptKey: namesByConceptKey) }
    }

    /// Medication names keyed by concept (nickname first, then display text).
    private func medicationNamesByConceptKey() async throws -> [String: String] {
        let medications = try await HKUserAnnotatedMedicationQueryDescriptor(predicate: nil, limit: nil)
            .result(for: healthStore)
        var namesByConceptKey: [String: String] = [:]
        for medication in medications {
            let key = Self.conceptKey(medication.medication.identifier)
            if namesByConceptKey[key] == nil {
                namesByConceptKey[key] = medication.nickname ?? medication.medication.displayText
            }
        }
        return namesByConceptKey
    }

    private static func summary(
        for event: HKMedicationDoseEvent,
        namesByConceptKey: [String: String]
    ) -> HealthKitMedicationDoseEventSummary {
        let key = conceptKey(event.medicationConceptIdentifier)
        return HealthKitMedicationDoseEventSummary(
            uuid: event.uuid,
            medicationName: namesByConceptKey[key] ?? "concept \(key)",
            conceptKey: key,
            status: status(for: event.logStatus),
            statusRaw: event.logStatus.rawValue,
            start: event.startDate,
            scheduled: event.scheduledDate,
            dose: event.doseQuantity,
            unit: event.unit.unitString
        )
    }

    /// Dose events deleted or added since `anchorCursorValue`. HealthKit reports deletions only
    /// through an anchored query; a plain window read never sees them. A corrected log replaces
    /// the object, so the old uuid arrives here as a deletion and the replacement as an addition
    /// (which the time-window read misses when the dose time is older than the replay window).
    public func readMedicationDoseEventChanges(
        anchorCursorValue: String?
    ) async throws -> HealthKitMedicationDoseEventChanges {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw HealthKitMedicationDoseEventReaderError.healthDataUnavailable
        }
        let anchor = try HealthKitAnchorCursorCodec.decodeOptional(anchorCursorValue)
        // Names are only needed to map added samples, which are ignored without a stored anchor.
        // A `let`, because the query's result handler is a concurrently executing closure.
        let namesByConceptKey: [String: String]
        let nameLookupFailed: Bool
        if anchor != nil {
            // Names map added samples only. A failed lookup refuses a read that has added
            // samples (the anchor does not advance, so nothing is lost), and never uploads
            // a placeholder name. A read with only deletions still delivers.
            if let names = try? await medicationNamesByConceptKey() {
                namesByConceptKey = names
                nameLookupFailed = false
            } else {
                namesByConceptKey = [:]
                nameLookupFailed = true
            }
        } else {
            namesByConceptKey = [:]
            nameLookupFailed = false
        }
        return try await executeCancellableHealthKitQuery(healthStore: healthStore) { completion in
            let query = HKAnchoredObjectQuery(
                type: HKObjectType.medicationDoseEventType(),
                predicate: nil,
                anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, addedSamples, deletedObjects, newAnchor, error in
                if let error {
                    completion(.failure(error))
                    return
                }
                guard let newAnchor else {
                    completion(.failure(HealthKitMedicationDoseEventReaderError.anchorUnavailable))
                    return
                }
                do {
                    let cursorValue = try HealthKitAnchorCursorCodec.encode(newAnchor)
                    let rawAdded = (addedSamples as? [HKMedicationDoseEvent]) ?? []
                    // A failed (transient) name lookup refuses a read with added samples: the
                    // anchor stays put, so the deletions are retried, and no placeholder name is
                    // uploaded in place of a real one. A concept the lookup succeeded without
                    // keeps the existing "concept <key>" name, so the read never stalls on it.
                    if anchor != nil, nameLookupFailed, !rawAdded.isEmpty {
                        completion(.failure(HealthKitMedicationDoseEventReaderError.nameLookupUnavailable))
                        return
                    }
                    let uuids = anchor == nil ? [] : (deletedObjects ?? []).map(\.uuid)
                    let added = anchor == nil
                        ? []
                        : ((addedSamples as? [HKMedicationDoseEvent]) ?? []).map {
                            Self.summary(for: $0, namesByConceptKey: namesByConceptKey)
                        }
                    completion(.success(HealthKitMedicationDoseEventChanges(
                        deletedUUIDs: uuids,
                        addedEvents: added,
                        anchorCursorValue: cursorValue
                    )))
                } catch {
                    completion(.failure(error))
                }
            }
            return query
        }
    }

    static func conceptKey(_ identifier: HKHealthConceptIdentifier) -> String {
        guard let data = try? NSKeyedArchiver.archivedData(withRootObject: identifier, requiringSecureCoding: true) else {
            return "unarchivable"
        }
        return HealthBridgeConceptKey.make(archived: data)
    }

    static func status(for value: HKMedicationDoseEvent.LogStatus) -> HealthBridgeMedicationDoseEvent.Status {
        switch value {
        case .taken: return .taken
        case .skipped: return .skipped
        case .notInteracted: return .notInteracted
        case .snoozed: return .snoozed
        case .notLogged: return .notLogged
        case .notificationNotSent: return .notificationNotSent
        @unknown default: return .unknown
        }
    }
}
#endif
