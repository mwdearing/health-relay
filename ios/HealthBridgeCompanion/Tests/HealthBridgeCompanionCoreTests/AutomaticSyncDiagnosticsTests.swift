import XCTest
@testable import HealthBridgeCompanionCore

/// Counts observer acknowledgements from whichever thread delivered them: the completion handler
/// must not need the main actor.
final class AcknowledgementCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var acknowledged = 0

    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return acknowledged
    }

    func increment() {
        lock.lock()
        acknowledged += 1
        lock.unlock()
    }

    /// Waits for the acknowledgement without touching the main actor.
    func waitForCount(_ target: Int, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if count >= target { return true }
            usleep(5_000)
        }
        return count >= target
    }
}

/// Records the remaining time each deadline wait is given.
final class DeadlineDelayRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [TimeInterval] = []

    var delays: [TimeInterval] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    func record(_ delay: TimeInterval) {
        lock.lock()
        recorded.append(delay)
        lock.unlock()
    }
}

final class AutomaticSyncDiagnosticsTests: XCTestCase {
    /// A deadline whose wait returns as soon as it is scheduled, so tests exercise the deadline
    /// path without waiting out the production 15 seconds.
    private func immediateDeadline(
        _ counter: AcknowledgementCounter,
        onDeadline: @escaping @Sendable () -> Void = {}
    ) -> ObserverAcknowledgementDeadline {
        ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { counter.increment() },
            onDeadline: onDeadline,
            sleep: { _ in await Task.yield() }
        )
    }
    @MainActor
    func testObserverAcknowledgesBeforeContinuationAndDiagnosticPersistence() async {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode)
        )
        draft.noteRunAccepted()
        draft.noteCompletion(.completed)
        var events: [String] = []
        var resumeContinuation: CheckedContinuation<Void, Never>?
        let startedAt = Date(timeIntervalSince1970: 1_788_000_000)
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement {
                events.append("acknowledge")
                XCTAssertFalse(
                    FileManager.default.fileExists(atPath: fileURL.path),
                    "Diagnostic persistence must not begin before HealthKit is acknowledged."
                )
            }
        )

        let processing = Task { @MainActor in
            await AutomaticSyncObserverEventLifecycle.process(
                startedAt: startedAt,
                now: { startedAt.addingTimeInterval(0.25) },
                deadline: deadline,
                admissionHandler: {
                    events.append("admission")
                    return .continueProcessing
                },
                eventHandler: {
                    events.append("continuation started")
                    await withCheckedContinuation { continuation in
                        resumeContinuation = continuation
                    }
                    events.append("continuation finished")
                    return draft
                },
                persistDiagnostic: { completedDraft, latency in
                    events.append("persist")
                    completedDraft.noteObserverCompletionLatency(latency)
                    XCTAssertTrue(store.recordFinal(completedDraft.record))
                }
            )
        }

        while resumeContinuation == nil { await Task.yield() }
        XCTAssertEqual(events, ["admission", "acknowledge", "continuation started"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))
        resumeContinuation?.resume()
        await processing.value

        XCTAssertEqual(events, [
            "admission", "acknowledge", "continuation started", "continuation finished", "persist",
        ])
        XCTAssertEqual(store.latestRecord?.observerCompletionLatencyBucket, .underOneSecond)
    }

    @MainActor
    func testObserverAdmissionCanFinishWithoutStartingContinuation() async {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode)
        )
        var events: [String] = []
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { events.append("acknowledge") }
        )
        await AutomaticSyncObserverEventLifecycle.process(
            startedAt: Date(timeIntervalSince1970: 1_788_000_000),
            deadline: deadline,
            admissionHandler: {
                events.append("admission")
                return .complete(draft)
            },
            eventHandler: {
                events.append("continuation")
                return nil
            },
            persistDiagnostic: { _, _ in events.append("persist") }
        )
        XCTAssertEqual(events, ["admission", "acknowledge", "persist"])
    }

    @MainActor
    func testObserverDurableAdmissionFailureAcknowledgesWithoutStartingContinuation() async {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode)
        )
        var events: [String] = []
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { events.append("acknowledge") }
        )
        await AutomaticSyncObserverEventLifecycle.process(
            startedAt: Date(timeIntervalSince1970: 1_788_000_000),
            deadline: deadline,
            admissionHandler: {
                events.append("admission")
                return .complete(draft)
            },
            eventHandler: {
                events.append("continuation")
                return nil
            },
            persistDiagnostic: { _, _ in events.append("persist") }
        )
        XCTAssertEqual(events, ["admission", "acknowledge", "persist"])
    }

    @MainActor
    func testObserverContinuationAcknowledgesBeforeAcquisition() async {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.steps.typeCode)
        )
        var events: [String] = []
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { events.append("acknowledge") }
        )
        await AutomaticSyncObserverEventLifecycle.process(
            startedAt: Date(timeIntervalSince1970: 1_788_000_000),
            deadline: deadline,
            admissionHandler: {
                events.append("admission")
                return .continueProcessing
            },
            eventHandler: {
                events.append("acquisition")
                return draft
            },
            persistDiagnostic: { _, _ in events.append("persist") }
        )

        XCTAssertEqual(
            events,
            ["admission", "acknowledge", "acquisition", "persist"]
        )
    }

    func testHistoryEvictsOldestRecordsAtBound() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(
            fileURL: fileURL,
            maximumRecordCount: 3
        )

        for remaining in 0..<5 {
            XCTAssertTrue(
                store.record(makeRecord(remainingPendingLaneCount: remaining))
            )
        }

        XCTAssertEqual(
            store.history.map(\.remainingPendingLaneCount),
            [2, 3, 4]
        )
    }

    func testMissingAndCorruptFilesRecoverWithoutThrowing() throws {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let missingStore = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        XCTAssertTrue(missingStore.history.isEmpty)

        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data("not-json".utf8).write(to: fileURL, options: .atomic)
        let corruptStore = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        XCTAssertTrue(corruptStore.history.isEmpty)

        XCTAssertTrue(
            corruptStore.record(makeRecord(remainingPendingLaneCount: 1))
        )
        XCTAssertEqual(corruptStore.history.count, 1)
    }

    @MainActor
    func testHangingAdmissionStillAcknowledgesByTheDeadline() async {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode)
        )
        draft.noteRunAccepted()
        draft.noteCompletion(.completed)
        let acknowledgementCount = AcknowledgementCounter()
        var eventHandlerRan = false
        var persistedBucket: AutomaticSyncObserverCompletionLatencyBucket?
        var releaseAdmission: CheckedContinuation<Void, Never>?
        let startedAt = Date(timeIntervalSince1970: 1_788_000_000)
        let deadline = immediateDeadline(acknowledgementCount)
        // Started from outside the main-actor hop, exactly as the HealthKit callback does.
        deadline.start(startedAt: startedAt, deadline: 15)

        let processing = Task { @MainActor in
            await AutomaticSyncObserverEventLifecycle.process(
                startedAt: startedAt,
                now: { startedAt.addingTimeInterval(15) },
                deadline: deadline,
                admissionHandler: {
                    await withCheckedContinuation { continuation in
                        releaseAdmission = continuation
                    }
                    return .continueProcessing
                },
                eventHandler: {
                    eventHandlerRan = true
                    return draft
                },
                persistDiagnostic: { completedDraft, _ in
                    persistedBucket = completedDraft.record.observerCompletionLatencyBucket
                }
            )
        }

        while releaseAdmission == nil || acknowledgementCount.count == 0 { await Task.yield() }
        XCTAssertEqual(
            acknowledgementCount.count,
            1,
            "A hung admission cycle must still acknowledge the HealthKit observer wake-up at the deadline."
        )
        XCTAssertFalse(eventHandlerRan)

        releaseAdmission?.resume()
        await processing.value
        XCTAssertEqual(acknowledgementCount.count, 1)
        XCTAssertTrue(eventHandlerRan)
        XCTAssertEqual(
            persistedBucket,
            .deadline,
            "The diagnostic must show that the deadline, not a finished admission, acknowledged HealthKit."
        )
    }

    @MainActor
    func testAdmissionFinishingAfterTheDeadlineAcknowledgesExactlyOnce() async {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.steps.typeCode)
        )
        draft.noteRunAccepted()
        draft.noteCompletion(.completed)
        let acknowledgementCount = AcknowledgementCounter()
        var releaseAdmission: CheckedContinuation<Void, Never>?
        let startedAt = Date(timeIntervalSince1970: 1_788_000_000)
        let deadline = immediateDeadline(acknowledgementCount)
        deadline.start(startedAt: startedAt, deadline: 15)

        let processing = Task { @MainActor in
            await AutomaticSyncObserverEventLifecycle.process(
                startedAt: startedAt,
                now: { startedAt.addingTimeInterval(16) },
                deadline: deadline,
                admissionHandler: {
                    await withCheckedContinuation { continuation in
                        releaseAdmission = continuation
                    }
                    return .complete(nil)
                },
                eventHandler: { draft },
                persistDiagnostic: { _, _ in }
            )
        }

        while releaseAdmission == nil || acknowledgementCount.count == 0 { await Task.yield() }
        XCTAssertEqual(acknowledgementCount.count, 1)
        releaseAdmission?.resume()
        await processing.value

        XCTAssertEqual(
            acknowledgementCount.count,
            1,
            "HealthKit must be acknowledged exactly once even when admission finishes after the deadline."
        )
        XCTAssertTrue(deadline.acknowledgedAtDeadline)
    }

    /// The deadline timer must not depend on the main actor: HealthKit's own callback starts it, and
    /// a blocked main actor must not be able to delay or skip the acknowledgement.
    func testDeadlineAcknowledgesWhileTheMainActorIsBlocked() {
        let acknowledgementCount = AcknowledgementCounter()
        let mainActorIsBlocked = DispatchSemaphore(value: 0)
        let releaseMainActor = DispatchSemaphore(value: 0)
        let finished = expectation(description: "deadline check finished")
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { acknowledgementCount.increment() },
            sleep: { _ in await Task.yield() }
        )

        DispatchQueue.global().async {
            DispatchQueue.main.async {
                // Occupy the main actor until the deadline has had every chance to fire.
                mainActorIsBlocked.signal()
                releaseMainActor.wait()
            }
            mainActorIsBlocked.wait()
            deadline.start(startedAt: Date(), deadline: 0.01)
            let firedWhileBlocked = acknowledgementCount.waitForCount(1, timeout: 3)
            releaseMainActor.signal()
            XCTAssertTrue(
                firedWhileBlocked,
                "The acknowledgement deadline must fire without the main actor."
            )
            finished.fulfill()
        }

        wait(for: [finished], timeout: 10)
        XCTAssertEqual(acknowledgementCount.count, 1)
        XCTAssertTrue(deadline.acknowledgedAtDeadline)
    }

    /// Time spent before the timer starts counts against the deadline, so a backlogged main actor
    /// cannot hand the wake-up a second full budget.
    func testDeadlineSubtractsTimeAlreadyElapsedSinceTheObserverCallback() async {
        let acknowledgementCount = AcknowledgementCounter()
        let recordedDelays: DeadlineDelayRecorder = DeadlineDelayRecorder()
        let startedAt = Date(timeIntervalSince1970: 1_788_000_000)
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { acknowledgementCount.increment() },
            sleep: { delay in recordedDelays.record(delay) },
            now: { startedAt.addingTimeInterval(10) }
        )

        deadline.start(startedAt: startedAt, deadline: 15)

        // The timer runs in a detached task, so wait for it rather than asserting straight after start().
        // Bounded by time rather than by yields: a busy CI host may not schedule the detached task within a
        // fixed number of yields.
        for _ in 0..<500 where acknowledgementCount.count == 0 { try? await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertEqual(recordedDelays.delays.count, 1)
        XCTAssertEqual(recordedDelays.delays.first ?? -1, 5, accuracy: 1)
        XCTAssertEqual(acknowledgementCount.count, 1)
    }

    /// A wake-up whose budget is already spent is acknowledged at once rather than being slept on.
    func testExpiredDeadlineAcknowledgesImmediatelyWithoutSleeping() {
        let acknowledgementCount = AcknowledgementCounter()
        let recordedDelays: DeadlineDelayRecorder = DeadlineDelayRecorder()
        let startedAt = Date(timeIntervalSince1970: 1_788_000_000)
        let deadline = ObserverAcknowledgementDeadline(
            acknowledgement: BackgroundObserverAcknowledgement { acknowledgementCount.increment() },
            sleep: { delay in recordedDelays.record(delay) },
            now: { startedAt.addingTimeInterval(20) }
        )

        deadline.start(startedAt: startedAt, deadline: 15)

        XCTAssertTrue(
            recordedDelays.delays.isEmpty,
            "An already-expired deadline must not start a wait that outlives the budget."
        )
        XCTAssertEqual(acknowledgementCount.count, 1)
        XCTAssertTrue(deadline.acknowledgedAtDeadline)
    }

    /// The engine creates the run's draft after admission, so the deadline outcome must be recorded
    /// by run identifier for that later draft to find.
    /// The re-arm debounce must survive an app relaunch, otherwise a quick restart re-arms on every
    /// foreground and floods the activity log.
    func testRearmDebounceSurvivesRelaunchThroughTheSettingsStore() throws {
        let suiteName = "healthBridgeTests.rearmDebounce.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let firstRearm = Date(timeIntervalSince1970: 1_788_000_000)

        XCTAssertNil(
            BackgroundSyncSettingsStore(userDefaults: defaults).lastBackgroundDeliveryRearmAt()
        )
        BackgroundSyncSettingsStore(userDefaults: defaults)
            .recordBackgroundDeliveryRearm(at: firstRearm)

        // A relaunch reads the persisted timestamp back through a brand new store instance.
        let persisted = BackgroundSyncSettingsStore(userDefaults: defaults)
            .lastBackgroundDeliveryRearmAt()
        XCTAssertEqual(
            persisted?.timeIntervalSince1970 ?? 0,
            firstRearm.timeIntervalSince1970,
            accuracy: 1
        )
        XCTAssertFalse(
            BackgroundDeliveryRearmPolicy.admitsRearm(
                lastRearmAt: persisted,
                now: firstRearm.addingTimeInterval(60)
            ),
            "A relaunch inside the debounce window must not re-arm again."
        )
        XCTAssertTrue(
            BackgroundDeliveryRearmPolicy.admitsRearm(
                lastRearmAt: persisted,
                now: firstRearm.addingTimeInterval(BackgroundDeliveryRearmPolicy.minimumInterval)
            )
        )
    }

    /// A wake-up answered at the deadline must stay visible when several observer callbacks coalesce
    /// into one engine run, because the merge drops one of the run identifiers.
    func testCoalescedObserverCallbacksKeepADeadlineAcknowledgement() {
        let registry = ObserverAcknowledgementDeadlineRegistry()
        let survivingRunID = UUID()
        let coalescedRunID = UUID()

        registry.noteAcknowledgedAtDeadline(runID: coalescedRunID)
        registry.absorbDeadlineAcknowledgement(
            from: coalescedRunID,
            into: survivingRunID
        )

        let batchDraft = AutomaticSyncDiagnosticDraft(
            reason: .observerBatch(typeCodes: ["sleep_analysis", "steps"]),
            runID: survivingRunID
        )
        XCTAssertTrue(registry.containsDeadlineAcknowledgement(runID: survivingRunID))
        batchDraft.noteObserverAcknowledgedAtDeadline()
        XCTAssertEqual(
            batchDraft.record.observerCompletionLatencyBucket,
            .deadline,
            "A batch run must report the deadline when any coalesced callback hit it."
        )
        XCTAssertFalse(registry.containsDeadlineAcknowledgement(runID: coalescedRunID))
    }

    /// A burst that hits every observed type at once must not evict a mark before its run looks it up:
/// the old 64-entry bound was smaller than the number of eligible types.
func testDeadlineRegistrySurvivesABurstLargerThanTheOldBound() {
    let registry = ObserverAcknowledgementDeadlineRegistry()
    let observedTypeCount = 200
    registry.reserveCapacity(forObserverCount: observedTypeCount)
    let runIDs = (0..<observedTypeCount).map { _ in UUID() }

    for runID in runIDs {
        registry.noteAcknowledgedAtDeadline(runID: runID)
    }

    let missing = runIDs.filter { !registry.containsDeadlineAcknowledgement(runID: $0) }
    XCTAssertTrue(
        missing.isEmpty,
        "A burst covering every observed type must keep every deadline mark: \(missing.count) were evicted."
    )
    XCTAssertGreaterThan(
        ObserverAcknowledgementDeadlineRegistry.defaultCapacity,
        64,
        "The default bound must exceed the number of eligible observed types."
    )
}

/// Marks are only dropped once they are older than the retention window, not merely because newer
/// ones arrived.
func testDeadlineRegistryEvictsOnlyMarksOlderThanTheRetentionWindow() {
    let now = Date(timeIntervalSince1970: 1_788_000_000)
    let registry = ObserverAcknowledgementDeadlineRegistry(
        capacity: 2,
        retention: 600,
        now: { now }
    )
    let staleRunID = UUID()
    registry.noteAcknowledgedAtDeadline(runID: staleRunID)

    // Two newer marks push the registry past its bound; the oldest mark is evicted first.
    let keptRunIDs = [UUID(), UUID()]
    for runID in keptRunIDs {
        registry.noteAcknowledgedAtDeadline(runID: runID)
    }
    XCTAssertTrue(
        keptRunIDs.allSatisfy { registry.containsDeadlineAcknowledgement(runID: $0) }
    )

    // Move past the retention window and the mark is gone even though nothing else arrived.
    // The mark is recorded at the original time and looked up after the clock has moved past retention.
    let clock = RegistryTestClock(now)
    let expiredRegistry = ObserverAcknowledgementDeadlineRegistry(
        capacity: 512,
        retention: 600,
        now: { clock.current }
    )
    expiredRegistry.noteAcknowledgedAtDeadline(runID: staleRunID)
    XCTAssertTrue(expiredRegistry.containsDeadlineAcknowledgement(runID: staleRunID))
    clock.advance(by: 1_200)
    XCTAssertFalse(expiredRegistry.containsDeadlineAcknowledgement(runID: staleRunID))
    XCTAssertEqual(
        ObserverAcknowledgementDeadlineRegistry.defaultRetention,
        3_600,
        "The default retention must outlast a sync run."
    )
}

func testDeadlineOutcomeReachesTheLaterEngineOwnedDraft() {
        let registry = ObserverAcknowledgementDeadlineRegistry()
        let acknowledgedRunID = UUID()
        let ordinaryRunID = UUID()

        registry.noteAcknowledgedAtDeadline(runID: acknowledgedRunID)

        XCTAssertTrue(registry.containsDeadlineAcknowledgement(runID: acknowledgedRunID))
        XCTAssertFalse(registry.containsDeadlineAcknowledgement(runID: ordinaryRunID))

        let deadlineDraft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode),
            runID: acknowledgedRunID
        )
        deadlineDraft.noteObserverAcknowledgedAtDeadline()
        let ordinaryDraft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode),
            runID: ordinaryRunID
        )
        ordinaryDraft.noteObserverAcknowledged()

        XCTAssertEqual(deadlineDraft.record.observerCompletionLatencyBucket, .deadline)
        XCTAssertEqual(ordinaryDraft.record.observerCompletionLatencyBucket, .notApplicable)
    }

    @MainActor
    func testDiagnosticRecordsDeadlineAcknowledgement() {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode)
        )
        draft.noteRunAccepted()
        draft.noteCompletion(.completed)
        draft.noteObserverAcknowledgedAtDeadline()
        // A later latency write from the finished cycle must not hide the deadline acknowledgement.
        draft.noteObserverCompletionLatency(0.2)

        XCTAssertEqual(draft.record.observerCompletionLatencyBucket, .deadline)
        XCTAssertFalse(draft.defersPersistenceUntilObserverAcknowledgement)
        XCTAssertTrue(draft.record.latestLaneSummary.contains("observer completion=deadline"))
    }

    @MainActor
    func testAcknowledgementDeadlineFitsInsideTheBackgroundWakeWindow() {
        XCTAssertEqual(
            BackgroundObserverAcknowledgementPolicy.observerAcknowledgementDeadline,
            15
        )
        XCTAssertLessThanOrEqual(
            BackgroundObserverAcknowledgementPolicy.observerAcknowledgementDeadline,
            20
        )
    }

    func testPendingLaneAgeUsesCoarseObservedDurationBuckets() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let firstSeen = Date(timeIntervalSince1970: 1_788_000_000)

        let initial = store.pendingSnapshot(
            pendingTypeCodes: [HealthBridgeHealthType.sleepAnalysis.typeCode],
            now: firstSeen
        )
        let aged = store.pendingSnapshot(
            pendingTypeCodes: [HealthBridgeHealthType.sleepAnalysis.typeCode],
            now: firstSeen.addingTimeInterval(25 * 60 * 60)
        )

        XCTAssertEqual(initial.oldestPendingLaneAgeBucket, .unknown)
        XCTAssertEqual(aged.pendingLaneCount, 1)
        XCTAssertEqual(aged.oldestPendingLane, .sleep)
        XCTAssertEqual(aged.oldestPendingLaneAgeBucket, .oneToThreeDays)
    }

    func testFailureRecoveryKeepsPendingAgeWhenDurableReloadIsUnavailable() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let firstSeen = Date(timeIntervalSince1970: 1_788_000_000)
        _ = store.pendingSnapshot(
            pendingTypeCodes: ["sleep_analysis"],
            now: firstSeen
        )
        let remaining = store.pendingSnapshot(
            pendingTypeCodes: ["sleep_analysis"],
            now: firstSeen.addingTimeInterval(25 * 60 * 60)
        )

        XCTAssertEqual(remaining.pendingLaneCount, 1)
        XCTAssertEqual(remaining.oldestPendingLane, .sleep)
        XCTAssertEqual(remaining.oldestPendingLaneAgeBucket, .oneToThreeDays)
    }

    func testQuantityPendingAgeTracksOnlyTheCoarseLane() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let firstSeen = Date(timeIntervalSince1970: 1_788_000_000)

        _ = store.pendingSnapshot(
            pendingTypeCodes: ["heart_rate"],
            now: firstSeen
        )
        let replacement = store.pendingSnapshot(
            pendingTypeCodes: ["respiratory_rate"],
            now: firstSeen.addingTimeInterval(25 * 60 * 60)
        )

        XCTAssertEqual(replacement.pendingLaneCount, 1)
        XCTAssertEqual(replacement.oldestPendingLane, .quantity)
        XCTAssertEqual(replacement.oldestPendingLaneAgeBucket, .oneToThreeDays)
    }

    func testRecoveryScrubsLegacyNonLanePendingKeysFromDisk() throws {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let fixture = """
        {
          "pendingSinceBucketByLane": {
            "quantity": 1986666,
            "quantity:legacy-deterministic-hash": 1986665
          },
          "records": [],
          "version": 1
        }
        """
        try Data(fixture.utf8).write(to: fileURL, options: .atomic)

        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        XCTAssertTrue(store.history.isEmpty)

        let persisted = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: fileURL))
                as? [String: Any]
        )
        let pendingKeys = try XCTUnwrap(
            persisted["pendingSinceBucketByLane"] as? [String: Int]
        )
        XCTAssertEqual(Set(pendingKeys.keys), ["quantity"])
    }

    func testLatestLaneRenderingOmitsPrivateValuesAndIdentifiers() {
        let record = makeRecord(remainingPendingLaneCount: 1)

        XCTAssertEqual(
            record.latestLaneSummary,
            "trigger=observer/sleep; admission=accepted; selected=quantity; pending=2; oldest=sleep (observed pending 1–6h); outcome=completed; remaining=1; observer completion=5–30s"
        )
        XCTAssertFalse(record.latestLaneSummary.contains(record.runID.uuidString))
    }

    func testObserverCompletionLatencyUpdatesOnlyTheMatchingRun() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let sleepRunID = UUID()
        let stepsRunID = UUID()
        XCTAssertTrue(
            store.record(
                makeRecord(
                    runID: sleepRunID,
                    triggerLane: .sleep,
                    observerCompletionLatencyBucket: .pending,
                    remainingPendingLaneCount: 2
                )
            )
        )
        XCTAssertTrue(
            store.record(
                makeRecord(
                    runID: stepsRunID,
                    triggerLane: .steps,
                    observerCompletionLatencyBucket: .pending,
                    remainingPendingLaneCount: 1
                )
            )
        )

        XCTAssertTrue(
            store.noteObserverCompletionLatency(7, runID: sleepRunID)
        )

        XCTAssertEqual(
            store.history.map(\.observerCompletionLatencyBucket),
            [.fiveToThirtySeconds, .pending]
        )
    }

    func testAcceptedDeferredAndFailedOutcomesRemainDistinct() {
        let draft = AutomaticSyncDiagnosticDraft(
            reason: .observer(typeCode: HealthBridgeHealthType.sleepAnalysis.typeCode)
        )
        draft.noteRunAccepted()
        XCTAssertEqual(draft.record.runOutcome, .accepted)

        draft.noteCompletion(.deferred)
        XCTAssertEqual(draft.record.runOutcome, .deferred)

        draft.noteCompletion(.failed)
        XCTAssertEqual(draft.record.runOutcome, .failed)
        XCTAssertEqual(draft.record.failure, .unknown)
    }

    func testFailureClassificationSeparatesStageCategoryAndCancellation() {
        for stage in [
            AutomaticSyncDiagnosticFailureStage.read,
            .store,
            .encoding,
            .transport,
        ] {
            XCTAssertEqual(
                AutomaticSyncDiagnosticFailure.classified(
                    stage: stage,
                    isCancellation: false
                ),
                AutomaticSyncDiagnosticFailure(
                    stage: stage,
                    category: .operationFailed
                )
            )
        }
        XCTAssertEqual(
            AutomaticSyncDiagnosticFailure.classified(
                stage: .transport,
                isCancellation: true
            ),
            AutomaticSyncDiagnosticFailure(
                stage: .transport,
                category: .cancellation
            )
        )
        XCTAssertEqual(
            AutomaticSyncDiagnosticFailure.classified(
                stage: .unknown,
                isCancellation: false
            ),
            .unknown
        )
    }

    func testTypedFailureRoundTripsWithoutAnArbitraryErrorStringField() throws {
        let rawPrivateError = "https://private.invalid/path Bearer synthetic-secret"
        let record = makeRecord(
            failure: AutomaticSyncDiagnosticFailure(
                stage: .encoding,
                category: .operationFailed
            ),
            remainingPendingLaneCount: 1
        )

        let data = try JSONEncoder().encode(record)
        let decoded = try JSONDecoder().decode(
            AutomaticSyncDiagnosticRecord.self,
            from: data
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        let failure = try XCTUnwrap(object["failure"] as? [String: String])

        XCTAssertEqual(decoded.failure, record.failure)
        XCTAssertEqual(failure, ["category": "operation_failed", "stage": "encoding"])
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains(rawPrivateError))
    }

    func testLatestLaneRenderingIncludesOnlyBoundedFailureMetadata() {
        let record = makeRecord(
            failure: AutomaticSyncDiagnosticFailure(
                stage: .read,
                category: .operationFailed
            ),
            remainingPendingLaneCount: 1
        )

        XCTAssertTrue(record.latestLaneSummary.contains("failure=read/operation_failed"))
        XCTAssertFalse(record.latestLaneSummary.contains("NSError"))
        XCTAssertFalse(record.latestLaneSummary.contains("http"))
    }

    func testUnknownFutureFailureEnumsDecodeAsUnknownWithoutDroppingRecord() throws {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let baseData = try JSONEncoder().encode(
            makeRecord(remainingPendingLaneCount: 1)
        )
        var record = try XCTUnwrap(
            JSONSerialization.jsonObject(with: baseData) as? [String: Any]
        )
        record["failure"] = [
            "category": "future_failure_category",
            "stage": "future_failure_stage",
        ]
        let snapshot: [String: Any] = [
            "pendingSinceBucketByLane": [:],
            "records": [record],
            "version": 1,
        ]
        try JSONSerialization.data(withJSONObject: snapshot)
            .write(to: fileURL, options: .atomic)

        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)

        XCTAssertEqual(store.history.count, 1)
        XCTAssertEqual(store.latestRecord?.failure, .unknown)
    }

    func testLegacyJSONWithoutFailureMetadataRetainsItsRecord() throws {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let legacyRecordData = try JSONEncoder().encode(
            makeRecord(remainingPendingLaneCount: 1)
        )
        var legacyRecord = try XCTUnwrap(
            JSONSerialization.jsonObject(with: legacyRecordData) as? [String: Any]
        )
        legacyRecord.removeValue(forKey: "failure")
        let snapshot: [String: Any] = [
            "pendingSinceBucketByLane": [:],
            "records": [legacyRecord],
            "version": 1,
        ]
        try JSONSerialization.data(withJSONObject: snapshot)
            .write(to: fileURL, options: .atomic)

        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)

        XCTAssertEqual(store.history.count, 1)
        XCTAssertNil(store.latestRecord?.failure)
    }

    func testFinalRecordPreservesTypedFailureOnAcceptedCheckpointReplacement() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let runID = UUID()
        XCTAssertTrue(
            store.recordAccepted(
                makeRecord(
                    runID: runID,
                    runOutcome: .accepted,
                    remainingPendingLaneCount: 1
                )
            )
        )

        XCTAssertTrue(
            store.recordFinal(
                makeRecord(
                    runID: runID,
                    runOutcome: .failed,
                    failure: AutomaticSyncDiagnosticFailure(
                        stage: .store,
                        category: .operationFailed
                    ),
                    remainingPendingLaneCount: 1
                )
            )
        )
        XCTAssertEqual(
            store.latestRecord?.failure,
            AutomaticSyncDiagnosticFailure(
                stage: .store,
                category: .operationFailed
            )
        )
    }

    func testFinalRecordReplacesOnlyItsDurableAcceptedCheckpoint() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let acceptedRunID = UUID()
        XCTAssertTrue(
            store.recordAccepted(
                makeRecord(
                    runID: acceptedRunID,
                    runOutcome: .accepted,
                    remainingPendingLaneCount: 2
                )
            )
        )

        XCTAssertTrue(
            store.recordFinal(
                makeRecord(
                    runID: acceptedRunID,
                    runOutcome: .completed,
                    remainingPendingLaneCount: 1
                )
            )
        )

        XCTAssertEqual(store.history.count, 1)
        XCTAssertEqual(store.history.first?.runOutcome, .completed)
    }

    func testSkippedAttemptCannotReplaceAnotherRunsAcceptedCheckpoint() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(fileURL: fileURL)
        let acceptedRunID = UUID()
        let skippedRunID = UUID()
        XCTAssertTrue(
            store.recordAccepted(
                makeRecord(
                    runID: acceptedRunID,
                    runOutcome: .accepted,
                    remainingPendingLaneCount: 2
                )
            )
        )

        XCTAssertTrue(
            store.recordFinal(
                makeRecord(
                    runID: skippedRunID,
                    runOutcome: .skipped,
                    remainingPendingLaneCount: 2
                )
            )
        )

        XCTAssertEqual(store.history.count, 2)
        XCTAssertEqual(store.history[0].runID, acceptedRunID)
        XCTAssertEqual(store.history[0].runOutcome, .accepted)
        XCTAssertEqual(store.history[1].runID, skippedRunID)
        XCTAssertEqual(store.history[1].runOutcome, .skipped)
    }

    func testBoundedHistoryPreservesTheActiveAcceptedCheckpoint() {
        let fileURL = temporaryFileURL()
        defer { try? FileManager.default.removeItem(at: fileURL.deletingLastPathComponent()) }
        let store = AutomaticSyncDiagnosticStore(
            fileURL: fileURL,
            maximumRecordCount: 3
        )
        let acceptedRunID = UUID()
        XCTAssertTrue(
            store.recordAccepted(
                makeRecord(
                    runID: acceptedRunID,
                    runOutcome: .accepted,
                    remainingPendingLaneCount: 2
                )
            )
        )

        for _ in 0..<5 {
            XCTAssertTrue(
                store.record(
                    makeRecord(
                        runOutcome: .skipped,
                        remainingPendingLaneCount: 2
                    )
                )
            )
        }

        XCTAssertEqual(store.history.count, 3)
        XCTAssertTrue(store.history.contains(where: { $0.runID == acceptedRunID }))
    }

    private func makeRecord(
        runID: UUID = UUID(),
        triggerLane: AutomaticSyncDiagnosticLane = .sleep,
        runOutcome: AutomaticSyncDiagnosticRunOutcome = .completed,
        observerCompletionLatencyBucket: AutomaticSyncObserverCompletionLatencyBucket = .fiveToThirtySeconds,
        failure: AutomaticSyncDiagnosticFailure? = nil,
        remainingPendingLaneCount: Int
    ) -> AutomaticSyncDiagnosticRecord {
        AutomaticSyncDiagnosticRecord(
            runID: runID,
            wakeSource: .healthKitObserver,
            triggerReason: .observer,
            triggerLane: triggerLane,
            admissionResult: .accepted,
            selectedLane: .quantity,
            pendingLaneCount: 2,
            oldestPendingLane: .sleep,
            oldestPendingLaneAgeBucket: .oneToSixHours,
            runOutcome: runOutcome,
            observerCompletionLatencyBucket: observerCompletionLatencyBucket,
            failure: failure,
            remainingPendingLaneCount: remainingPendingLaneCount
        )
    }

    private func temporaryFileURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "automatic-sync-diagnostics-\(UUID().uuidString)",
                isDirectory: true
            )
            .appendingPathComponent("state.json")
    }
}

/// A clock a test can move forward; the registry reads it through a @Sendable closure.
private final class RegistryTestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Date

    init(_ start: Date) { value = start }

    var current: Date {
        lock.lock(); defer { lock.unlock() }
        return value
    }

    func advance(by seconds: TimeInterval) {
        lock.lock(); defer { lock.unlock() }
        value = value.addingTimeInterval(seconds)
    }
}
