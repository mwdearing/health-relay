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
/// live HealthKit store. Disable completions can be held open, and a disable can be reported as
/// failed, so the obsolete-re-arm and failed-disable paths are reachable from a test.
private final class RecordingDeliveryClient: BackgroundDeliveryRegistrationClient, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [String] = []
    private var pendingDisableCompletions: [@Sendable (Bool, Error?) -> Void] = []
    private let disableSucceeds: Bool
    private let disableError: Error?

    /// Set by tests that need a disable to stay in flight.
    var defersDisableCompletion = false

    /// Runs on the delivering thread just before the held disable completions are handed back, so a
    /// test can land a `stop()` in the window the re-arm's enable submit would otherwise use.
    var beforeDisableCompletionDelivery: (@Sendable () -> Void)?

    init(disableSucceeds: Bool = true, disableError: Error? = nil) {
        self.disableSucceeds = disableSucceeds
        self.disableError = disableError
    }

    var calls: [String] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    var hasOutstandingDisable: Bool {
        lock.lock()
        defer { lock.unlock() }
        return !pendingDisableCompletions.isEmpty
    }

    /// Answers every outstanding disable, as HealthKit eventually does.
    func completeOutstandingDisables() {
        lock.lock()
        let completions = pendingDisableCompletions
        pendingDisableCompletions.removeAll()
        lock.unlock()
        beforeDisableCompletionDelivery?()
        completions.forEach { $0(disableSucceeds, disableError) }
    }

    func disableBackgroundDelivery(
        for sampleType: HKSampleType,
        completion: @escaping @Sendable (Bool, Error?) -> Void
    ) {
        lock.lock()
        recorded.append("disable:\(sampleType.identifier)")
        if defersDisableCompletion {
            pendingDisableCompletions.append(completion)
            lock.unlock()
            return
        }
        lock.unlock()
        completion(disableSucceeds, disableError)
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
    private let stepCountIdentifier = HKObjectType.quantityType(forIdentifier: .stepCount)!.identifier
    private let sleepAnalysisIdentifier =
        HKObjectType.categoryType(forIdentifier: .sleepAnalysis)!.identifier

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
            "disable:\(sleepAnalysisIdentifier)",
            "enable:\(sleepAnalysisIdentifier)",
            "disable:\(stepCountIdentifier)",
            "enable:\(stepCountIdentifier)",
        ])
        for _ in 0..<10 where results.count < 2 { await Task.yield() }
        XCTAssertEqual(results.map(\.0), ["sleep_analysis", "steps"])
        XCTAssertEqual(results.map(\.1), [true, true])
    }

    func testRearmDoesNotEnableDeliveryAfterStopAdmission() async {
        let client = RecordingDeliveryClient()
        client.defersDisableCompletion = true
        let coordinator = HealthKitBackgroundDeliveryCoordinator(
            deliveryClient: client,
            isHealthDataAvailable: { true }
        )
        var resultCount = 0

        coordinator.rearmBackgroundDelivery(healthTypes: [.steps]) { _, _ in resultCount += 1 }
        XCTAssertTrue(client.hasOutstandingDisable)
        XCTAssertEqual(client.calls, ["disable:\(stepCountIdentifier)"])

        // Admission stops while the re-arm's disable is still outstanding.
        coordinator.stop(healthTypes: [.steps])
        client.completeOutstandingDisables()
        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(
            client.calls,
            ["disable:\(stepCountIdentifier)", "disable:\(stepCountIdentifier)"],
            "A re-arm whose disable completes after stop() must not enable delivery for a type that no longer has an observer query."
        )
        XCTAssertEqual(resultCount, 0)
    }

    /// `stop()` landing between the disable completion arriving and the enable submit must not leave
    /// delivery enabled: the generation check and the submit are one atomic step against `stop()`.
    func testStopBetweenDisableCompletionAndEnableNeverEnablesDelivery() async {
        let client = RecordingDeliveryClient()
        client.defersDisableCompletion = true
        let coordinator = HealthKitBackgroundDeliveryCoordinator(
            deliveryClient: client,
            isHealthDataAvailable: { true }
        )
        let deliveryIsParked = DispatchSemaphore(value: 0)
        let resumeDelivery = DispatchSemaphore(value: 0)
        var resultCount = 0

        // The fake store parks HealthKit's disable completion on another thread, so the stop below
        // lands in exactly the window the re-arm's enable submit would otherwise use.
        client.beforeDisableCompletionDelivery = {
            deliveryIsParked.signal()
            _ = resumeDelivery.wait(timeout: .now() + 5)
        }

        coordinator.rearmBackgroundDelivery(healthTypes: [.steps]) { _, _ in resultCount += 1 }
        XCTAssertTrue(client.hasOutstandingDisable)
        XCTAssertEqual(client.calls, ["disable:\(stepCountIdentifier)"])

        let delivery = Task.detached { client.completeOutstandingDisables() }
        XCTAssertEqual(
            deliveryIsParked.wait(timeout: .now() + 5),
            .success,
            "The fake store never reached the held disable completion."
        )
        coordinator.stop(healthTypes: [.steps])
        resumeDelivery.signal()
        await delivery.value
        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(
            client.calls,
            ["disable:\(stepCountIdentifier)", "disable:\(stepCountIdentifier)"],
            "The re-arm must not enable delivery for a type whose observer queries stop() already removed."
        )
        XCTAssertEqual(resultCount, 0)
    }

    /// The same invariant under a genuine race: whichever of the two runs first, the enable is
    /// either submitted before the stop's disable or never submitted at all.
    func testEnableIsAtomicWithStopAdmissionUnderConcurrentDelivery() async {
        for _ in 0..<50 {
            let client = RecordingDeliveryClient()
            client.defersDisableCompletion = true
            let coordinator = HealthKitBackgroundDeliveryCoordinator(
                deliveryClient: client,
                isHealthDataAvailable: { true }
            )

            coordinator.rearmBackgroundDelivery(healthTypes: [.steps]) { _, _ in }
            let delivery = Task.detached { client.completeOutstandingDisables() }
            coordinator.stop(healthTypes: [.steps])
            await delivery.value

            let calls = client.calls
            let stopDisableIndex = calls.lastIndex { $0.hasPrefix("disable:") } ?? -1
            for (index, call) in calls.enumerated() where call.hasPrefix("enable:") {
                XCTAssertLessThan(
                    index,
                    stopDisableIndex,
                    "An enable submitted after the stop's disable would leave delivery on with no observer."
                )
            }
            XCTAssertLessThanOrEqual(
                calls.filter { $0.hasPrefix("disable:") }.count,
                2
            )
        }
    }

    func testFailedDisableMakesTheRearmFail() async {
        let client = RecordingDeliveryClient(disableSucceeds: false)
        let coordinator = HealthKitBackgroundDeliveryCoordinator(
            deliveryClient: client,
            isHealthDataAvailable: { true }
        )
        var results: [(String, Bool)] = []

        coordinator.rearmBackgroundDelivery(healthTypes: [.steps]) { typeCode, succeeded in
            results.append((typeCode, succeeded))
        }
        for _ in 0..<10 where results.isEmpty { await Task.yield() }

        XCTAssertEqual(
            client.calls,
            ["disable:\(stepCountIdentifier)", "enable:\(stepCountIdentifier)"],
            "Enablement is still attempted so the registration is restored where possible."
        )
        XCTAssertEqual(results.map(\.0), ["steps"])
        XCTAssertEqual(
            results.map(\.1),
            [false],
            "A disable HealthKit rejected means the reset did not happen, so the re-arm failed."
        )
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