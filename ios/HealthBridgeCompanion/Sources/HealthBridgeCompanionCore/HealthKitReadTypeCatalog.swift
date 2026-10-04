#if canImport(HealthKit)
import HealthKit

public enum HealthKitReadTypeCatalog {
    public static func objectTypes(for healthTypes: [HealthBridgeHealthType]) -> Set<HKObjectType> {
        Set(healthTypes.compactMap { objectType(for: $0.typeCode) })
    }

    public static func sampleTypes(for healthTypes: [HealthBridgeHealthType]) -> [HKSampleType] {
        healthTypes.compactMap { objectType(for: $0.typeCode) as? HKSampleType }
    }

    /// Sample types for observer queries and background delivery. Unlike `sampleTypes(for:)` this
    /// includes the per-object medication dose event type, which must never reach
    /// `requestAuthorization` (it throws); observing it needs no read authorization.
    public static func observerSampleTypes(for healthTypes: [HealthBridgeHealthType]) -> [HKSampleType] {
        healthTypes.compactMap { healthType in
            if healthType.typeCode == HealthBridgeHealthType.medicationDoseEvents.typeCode {
                return medicationDoseEventSampleType()
            }
            return objectType(for: healthType.typeCode) as? HKSampleType
        }
    }

    /// nil before iOS 26, where the medication dose event type does not exist.
    private static func medicationDoseEventSampleType() -> HKSampleType? {
        if #available(iOS 26.0, macOS 26.0, *) {
            return HKObjectType.medicationDoseEventType()
        }
        return nil
    }

    public static func objectTypes(forTypeCodes typeCodes: [String]) -> [HKObjectType] {
        Array(Set(typeCodes))
            .compactMap(objectType(for:))
            .sorted { $0.identifier < $1.identifier }
    }

    public static func availableTypeCodes(forTypeCodes typeCodes: [String]) -> [String] {
        typeCodes
            .filter { objectType(for: $0) != nil }
            .sorted()
    }

    /// Type codes the automatic engine may schedule as lanes. Same as `availableTypeCodes` plus the
    /// medication dose event lane, which is schedulable (it reads without prompting) even though
    /// it is not an authorization type. Use this only for lane selection, never for authorization.
    public static func availableLaneTypeCodes(forTypeCodes typeCodes: [String]) -> [String] {
        typeCodes
            .filter { typeCode in
                if typeCode == HealthBridgeHealthType.medicationDoseEvents.typeCode {
                    return medicationDoseEventSampleType() != nil
                }
                return objectType(for: typeCode) != nil
            }
            .sorted()
    }

    public static func sampleTypes(forTypeCodes typeCodes: [String]) -> [HKSampleType] {
        objectTypes(forTypeCodes: typeCodes).compactMap { $0 as? HKSampleType }
    }

    private static func objectType(for typeCode: String) -> HKObjectType? {
        guard let entry = HealthKitTypeCatalog.entry(for: typeCode) else {
            return nil
        }
        switch entry.objectKind {
        case .quantity:
            return HealthKitQuantitySampleMapper.quantityType(for: entry)
        case .category:
            return categoryType(for: entry)
        case .workout:
            return HKObjectType.workoutType()
        case .electrocardiogram:
            return HKObjectType.electrocardiogramType()
        case .medicationDoseEvent:
            // Per-object authorization only; passing it to requestAuthorization throws.
            return nil
        }
    }

    private static func categoryType(for entry: HealthKitTypeCatalogEntry) -> HKCategoryType? {
        switch entry.typeCode {
        case "sleep_analysis":
            return HKObjectType.categoryType(forIdentifier: .sleepAnalysis)
        default:
            return nil
        }
    }
}

/// Seam over `HKHealthStore`'s background delivery registration calls so re-arm order can be
/// verified without a live HealthKit store.
public protocol BackgroundDeliveryRegistrationClient: AnyObject {
    func disableBackgroundDelivery(
        for sampleType: HKSampleType,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    )
    func enableBackgroundDelivery(
        for sampleType: HKSampleType,
        frequency: HKUpdateFrequency,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    )
}

extension HKHealthStore: BackgroundDeliveryRegistrationClient {
    public func disableBackgroundDelivery(
        for sampleType: HKSampleType,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    ) {
        disableBackgroundDelivery(for: sampleType, withCompletion: completion)
    }

    public func enableBackgroundDelivery(
        for sampleType: HKSampleType,
        frequency: HKUpdateFrequency,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    ) {
        enableBackgroundDelivery(for: sampleType, frequency: frequency, withCompletion: completion)
    }
}

/// Foreground re-arm cadence. A re-arm is cheap but pointless in bursts, and the activity log is
/// easier to read when it shows one re-arm per return to the foreground.
public enum BackgroundDeliveryRearmPolicy {
    public static let minimumInterval: TimeInterval = 600

    public static func admitsRearm(lastRearmAt: Date?, now: Date) -> Bool {
        guard let lastRearmAt else { return true }
        return now.timeIntervalSince(lastRearmAt) >= minimumInterval
    }
}

/// Carries the delivery client across its own completion callbacks, which HealthKit does not
/// guarantee to run on any particular isolation domain.
private final class BackgroundDeliveryClientBox: @unchecked Sendable {
    let client: any BackgroundDeliveryRegistrationClient

    init(_ client: any BackgroundDeliveryRegistrationClient) {
        self.client = client
    }
}

/// Carries the re-arm reporting closure across the HealthKit completion callbacks, which are not
/// main-actor isolated. Holding it in a main-actor type keeps the call itself on the main actor.
@MainActor
private final class BackgroundDeliveryRearmReporter {
    private let handler: @MainActor (String, Bool) -> Void

    init(handler: @escaping @MainActor (String, Bool) -> Void) {
        self.handler = handler
    }

    func report(typeCode: String, succeeded: Bool) {
        handler(typeCode, succeeded)
    }
}

public enum HealthKitAuthorizationError: Error, Equatable {
    case healthDataUnavailable
    case emptyReadTypeSet
}

public final class HealthStoreAuthorizer {
    private let healthStoreProvider: () -> HKHealthStore

    public init(healthStore: HKHealthStore? = nil) {
        if let healthStore {
            healthStoreProvider = { healthStore }
        } else {
            healthStoreProvider = { HKHealthStore() }
        }
    }

    public func requestReadAuthorization(healthTypes: [HealthBridgeHealthType]) async throws {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw HealthKitAuthorizationError.healthDataUnavailable
        }
        let readTypes = HealthKitReadTypeCatalog.objectTypes(for: healthTypes)
        guard !readTypes.isEmpty else {
            throw HealthKitAuthorizationError.emptyReadTypeSet
        }
        try await healthStoreProvider().requestAuthorization(toShare: Set<HKSampleType>(), read: readTypes)
    }

    public func requestReadAuthorization(typeCodes: [String]) async throws {
        let readTypes = try readTypesForAuthorization(typeCodes: typeCodes)
        try await healthStoreProvider().requestAuthorization(toShare: Set<HKSampleType>(), read: readTypes)
    }

    public func requestStatusForReadAuthorization(typeCodes: [String]) async throws -> HKAuthorizationRequestStatus {
        let readTypes = try readTypesForAuthorization(typeCodes: typeCodes)
        return try await healthStoreProvider().statusForAuthorizationRequest(toShare: Set<HKSampleType>(), read: readTypes)
    }

    private func readTypesForAuthorization(typeCodes: [String]) throws -> Set<HKObjectType> {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw HealthKitAuthorizationError.healthDataUnavailable
        }
        let readTypes = Set(HealthKitReadTypeCatalog.objectTypes(forTypeCodes: typeCodes))
        guard !readTypes.isEmpty else {
            throw HealthKitAuthorizationError.emptyReadTypeSet
        }
        return readTypes
    }
}

@MainActor
public final class HealthKitBackgroundDeliveryCoordinator {
    private let healthStore: HKHealthStore
    private var activeObserverQueries: [HKObserverQuery] = []
    private var callbackGeneration: UInt64 = 0
    private var registrationTypes: [String: HKSampleType] = [:]
    private var registrationHandler: @MainActor (String, Bool) -> Void = { _, _ in }
    private var isCurrent: @MainActor () -> Bool = { false }
    private let deliveryClient: BackgroundDeliveryRegistrationClient
    private let isHealthDataAvailable: @Sendable () -> Bool

    public init(
        healthStore: HKHealthStore = HKHealthStore(),
        deliveryClient: (any BackgroundDeliveryRegistrationClient)? = nil,
        isHealthDataAvailable: @escaping @Sendable () -> Bool = { HKHealthStore.isHealthDataAvailable() }
    ) {
        self.healthStore = healthStore
        self.deliveryClient = deliveryClient ?? healthStore
        self.isHealthDataAvailable = isHealthDataAvailable
    }

    public var activeObserverCount: Int {
        activeObserverQueries.count
    }

    public func start(
        healthTypes: [HealthBridgeHealthType] = HealthBridgeBackgroundSync.observedHealthTypes,
        registrationHandler: @escaping @MainActor (_ typeCode: String, _ succeeded: Bool) -> Void = { _, _ in },
        observerEntryHandler: @escaping @Sendable (_ typeCode: String, _ runID: UUID) -> Void = { _, _ in },
        isCurrent: @escaping @MainActor () -> Bool,
        observerAdmissionHandler: @escaping @MainActor (_ typeCode: String, _ runID: UUID) async -> AutomaticSyncObserverEventAdmission,
        observerCompletionHandler: @escaping @MainActor (AutomaticSyncDiagnosticDraft, TimeInterval) -> Void = { _, _ in },
        eventHandler: @escaping @MainActor (_ typeCode: String, _ runID: UUID) async -> AutomaticSyncDiagnosticDraft?
    ) {
        callbackGeneration &+= 1
        let expectedCallbackGeneration = callbackGeneration
        self.registrationHandler = registrationHandler
        self.isCurrent = isCurrent
        registrationTypes = [:]
        stopActiveObserverQueries()
        guard HKHealthStore.isHealthDataAvailable(), isCurrent() else { return }

        for healthType in healthTypes {
            guard let sampleType = HealthKitReadTypeCatalog.observerSampleTypes(for: [healthType]).first else {
                continue
            }
            registrationTypes[healthType.typeCode] = sampleType
            let observer = HKObserverQuery(sampleType: sampleType, predicate: nil) { [weak self] _, completionHandler, error in
                let runID = UUID()
                observerEntryHandler(healthType.typeCode, runID)
                let completion = BackgroundObserverAcknowledgement(completionHandler)
                let observerStartedAt = Date()
                guard error == nil else {
                    let completionLatency = Date().timeIntervalSince(observerStartedAt)
                    Task { @MainActor [weak self] in
                        guard let self, self.callbackGeneration == expectedCallbackGeneration,
                              self.isCurrent() else {
                            completion.call()
                            return
                        }
                        await AutomaticSyncObserverEventLifecycle.process(
                            startedAt: observerStartedAt,
                            admissionHandler: {
                                await observerAdmissionHandler(
                                    healthType.typeCode,
                                    runID
                                )
                            },
                            eventHandler: {
                                let diagnostic = AutomaticSyncDiagnosticDraft(
                                    observerFailureLane: BackgroundRecoveryLane(
                                        typeCode: healthType.typeCode
                                    ),
                                    runID: runID,
                                    completionLatency: completionLatency,
                                    durableState: .available
                                )
                                diagnostic.noteCompletion(.deferred)
                                _ = await eventHandler(healthType.typeCode, runID)
                                return diagnostic
                            },
                            acknowledge: completion.call,
                            persistDiagnostic: observerCompletionHandler
                        )
                    }
                    return
                }
                Task { @MainActor [weak self] in
                    guard let self, self.callbackGeneration == expectedCallbackGeneration,
                          self.isCurrent() else {
                        completion.call()
                        return
                    }
                    await AutomaticSyncObserverEventLifecycle.process(
                        startedAt: observerStartedAt,
                        admissionHandler: {
                            await observerAdmissionHandler(healthType.typeCode, runID)
                        },
                        eventHandler: {
                            await eventHandler(healthType.typeCode, runID)
                        },
                        acknowledge: completion.call,
                        persistDiagnostic: observerCompletionHandler
                    )
                }
            }
            healthStore.execute(observer)
            activeObserverQueries.append(observer)
        }
        disableRetiredRegistrations(observing: healthTypes)
        reconcileRegistrations()
    }


    public func reconcileRegistrations() {
        guard isCurrent() else { return }
        let expectedGeneration = callbackGeneration
        for (typeCode, sampleType) in registrationTypes {
            healthStore.enableBackgroundDelivery(
                for: sampleType,
                frequency: .immediate
            ) { [weak self] succeeded, error in
                let enabled = succeeded && error == nil
                Task { @MainActor [weak self] in
                    guard let self,
                          self.callbackGeneration == expectedGeneration,
                          self.isCurrent() else { return }
                    self.registrationHandler(typeCode, enabled)
                }
            }
        }
    }

    public func stop(
        healthTypes: [HealthBridgeHealthType] = HealthBridgeBackgroundSync.observedHealthTypes
    ) {
        callbackGeneration &+= 1
        isCurrent = { false }
        registrationTypes = [:]
        guard HKHealthStore.isHealthDataAvailable() else {
            activeObserverQueries.removeAll()
            return
        }
        stopActiveObserverQueries()

        let sampleTypes = HealthKitReadTypeCatalog.observerSampleTypes(for: healthTypes)
        for sampleType in sampleTypes {
            healthStore.disableBackgroundDelivery(for: sampleType) { _, _ in }
        }
    }

    /// Re-registers background delivery for the observed types without touching the observer
    /// queries: HealthKit can stop launching the app after long gaps or after missed
    /// acknowledgements, and a disable/enable pair restores the registration. Each type is
    /// re-armed in order, and its enable call only follows the disable completion for that type, so
    /// a re-arm never races the registration path in `start()`. Each result is reported through
    /// `registrationHandler` with the type code.
    public func rearmBackgroundDelivery(
        healthTypes: [HealthBridgeHealthType] = HealthBridgeBackgroundSync.observedHealthTypes,
        registrationHandler: @escaping @MainActor (_ typeCode: String, _ succeeded: Bool) -> Void = { _, _ in }
    ) {
        guard isHealthDataAvailable() else { return }
        let expectedGeneration = callbackGeneration
        let box = BackgroundDeliveryClientBox(deliveryClient)
        let reporter = BackgroundDeliveryRearmReporter(handler: registrationHandler)
        for healthType in healthTypes {
            guard let sampleType = HealthKitReadTypeCatalog.observerSampleTypes(for: [healthType]).first else {
                continue
            }
            let typeCode = healthType.typeCode
            box.client.disableBackgroundDelivery(for: sampleType) { _, _ in
                box.client.enableBackgroundDelivery(
                    for: sampleType,
                    frequency: .immediate
                ) { succeeded, error in
                    let enabled = succeeded && error == nil
                    Task { @MainActor [weak self] in
                        guard let self,
                              self.callbackGeneration == expectedGeneration else { return }
                        reporter.report(typeCode: typeCode, succeeded: enabled)
                    }
                }
            }
        }
    }

    /// Turns off background delivery an earlier build registered for a type this build no longer
    /// observes. Without an observer query, a delivery for that type is never acknowledged.
    private func disableRetiredRegistrations(observing healthTypes: [HealthBridgeHealthType]) {
        let observed = Set(healthTypes.map(\.typeCode))
        let retired = HealthBridgeBackgroundSync.retiredBackgroundDeliveryHealthTypes
            .filter { !observed.contains($0.typeCode) }
        for sampleType in HealthKitReadTypeCatalog.observerSampleTypes(for: retired) {
            healthStore.disableBackgroundDelivery(for: sampleType) { _, _ in }
        }
    }

    private func stopActiveObserverQueries() {
        for query in activeObserverQueries {
            healthStore.stop(query)
        }
        activeObserverQueries.removeAll()
    }
}
#endif
