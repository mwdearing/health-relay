import XCTest
@testable import HealthBridgeCompanionCore

#if canImport(HealthKit)
import HealthKit

final class HealthKitReadTypeCatalogTests: XCTestCase {
    func testSampleTypesForTypeCodesMapsBroadCatalogEntries() throws {
        let sampleTypes = HealthKitReadTypeCatalog.sampleTypes(forTypeCodes: [
            "sleep_analysis",
            "heart_rate_variability_sdnn",
            "active_energy",
            "workout",
            "unknown_metric",
            "active_energy",
        ])
        let identifiers = Set(sampleTypes.map(\.identifier))

        XCTAssertEqual(identifiers, Set([
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!.identifier,
            HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN)!.identifier,
            HKObjectType.quantityType(forIdentifier: .activeEnergyBurned)!.identifier,
            HKObjectType.workoutType().identifier,
        ]))
    }

    func testObjectTypesForTypeCodesRejectsUnknownEntries() {
        let objectTypes = HealthKitReadTypeCatalog.objectTypes(forTypeCodes: [
            "unknown_metric",
            "heart_rate",
        ])

        XCTAssertEqual(objectTypes.map(\.identifier), [
            HKObjectType.quantityType(forIdentifier: .heartRate)!.identifier,
        ])
    }

    func testAvailableTypeCodesFiltersRuntimeUnavailableEntries() {
        let typeCodes = HealthKitReadTypeCatalog.availableTypeCodes(forTypeCodes: [
            "unknown_metric",
            "heart_rate",
            "blood_alcohol_content",
        ])

        XCTAssertTrue(typeCodes.contains("heart_rate"))
        XCTAssertTrue(typeCodes.contains("blood_alcohol_content"))
        XCTAssertFalse(typeCodes.contains("unknown_metric"))
    }
}

/// Records the order of background delivery registration calls so re-arm can be verified without a
/// live HealthKit store.
private final class RecordingDeliveryClient: BackgroundDeliveryRegistrationClient, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [String] = []
    var calls: [String] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    func disableBackgroundDelivery(
        for sampleType: HKSampleType,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    ) {
        lock.lock()
        recorded.append("disable:\(sampleType.identifier)")
        lock.unlock()
        completion(true, nil)
    }

    func enableBackgroundDelivery(
        for sampleType: HKSampleType,
        frequency: HKUpdateFrequency,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    ) {
        lock.lock()
        recorded.append("enable:\(sampleType.identifier)")
        lock.unlock()
        completion(true, nil)
    }
}

@MainActor
final class HealthKitBackgroundDeliveryRearmTests: XCTestCase {
    func testRearmDisablesThenEnablesEachObservedTypeInOrder() async {
        let client = RecordingDeliveryClient()
        let coordinator = HealthKitBackgroundDeliveryCoordinator(
            deliveryClient: client,
            isHealthDataAvailable: { true }
        )
        var results: [(String, Bool)] = []

        coordinator.rearmBackgroundDelivery(
            healthTypes: [.sleepAnalysis, .steps]
        ) { typeCode, succeeded in
            results.append((typeCode, succeeded))
        }

        XCTAssertEqual(client.calls, [
            "disable:\(HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!.identifier)",
            "enable:\(HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!.identifier)",
            "disable:\(HKObjectType.quantityType(forIdentifier: .stepCount)!.identifier)",
            "enable:\(HKObjectType.quantityType(forIdentifier: .stepCount)!.identifier)",
        ])
        for _ in 0..<10 where results.count < 2 { await Task.yield() }
        XCTAssertEqual(results.map(\.0), ["sleep_analysis", "steps"])
        XCTAssertEqual(results.map(\.1), [true, true])
    }

    func testRearmPolicyAllowsOnePerForegroundSessionAndNeverMoreOftenThanTenMinutes() {
        let first = Date(timeIntervalSince1970: 1_788_000_000)

        XCTAssertTrue(BackgroundDeliveryRearmPolicy.admitsRearm(lastRearmAt: nil, now: first))
        XCTAssertFalse(
            BackgroundDeliveryRearmPolicy.admitsRearm(
                lastRearmAt: first,
                now: first.addingTimeInterval(60)
            ),
            "Repeated activations inside the debounce window must not re-arm again."
        )
        XCTAssertTrue(
            BackgroundDeliveryRearmPolicy.admitsRearm(
                lastRearmAt: first,
                now: first.addingTimeInterval(BackgroundDeliveryRearmPolicy.minimumInterval)
            )
        )
        XCTAssertEqual(BackgroundDeliveryRearmPolicy.minimumInterval, 600)
    }

    func testRearmIsInertWhenHealthDataIsUnavailable() {
        let client = RecordingDeliveryClient()
        let coordinator = HealthKitBackgroundDeliveryCoordinator(
            deliveryClient: client,
            isHealthDataAvailable: { false }
        )
        var resultCount = 0

        coordinator.rearmBackgroundDelivery(healthTypes: [.steps]) { _, _ in resultCount += 1 }

        XCTAssertTrue(client.calls.isEmpty)
        XCTAssertEqual(resultCount, 0)
    }
}
#endif
