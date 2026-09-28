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

        let medications = try await HKUserAnnotatedMedicationQueryDescriptor(predicate: nil, limit: nil)
            .result(for: healthStore)
        var namesByConceptKey: [String: String] = [:]
        for medication in medications {
            let key = Self.conceptKey(medication.medication.identifier)
            if namesByConceptKey[key] == nil {
                namesByConceptKey[key] = medication.nickname ?? medication.medication.displayText
            }
        }

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

        return events.map { event in
            let key = Self.conceptKey(event.medicationConceptIdentifier)
            return HealthKitMedicationDoseEventSummary(
                uuid: event.uuid,
                medicationName: namesByConceptKey[key] ?? "concept \(key)",
                conceptKey: key,
                status: Self.status(for: event.logStatus),
                statusRaw: event.logStatus.rawValue,
                start: event.startDate,
                scheduled: event.scheduledDate,
                dose: event.doseQuantity,
                unit: event.unit.unitString
            )
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
