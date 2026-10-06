import Foundation
#if canImport(HealthKit)
import HealthKit
#endif
#if os(iOS)
import UIKit
#endif

@MainActor
final class AutomaticSyncRuntime {
    unowned let viewModel: HealthBridgeCompanionViewModel

    /// True only once the coordinator actually holds at least one observer query. `start()` can
    /// return before registering anything, and enabling background delivery in that state makes
    /// HealthKit launch the app with nothing to acknowledge the wake-up (health-relay #78).
    private var isActivated = false
    /// Set before observers are started and cleared by `stopAdmission()`. The observer start
    /// predicate reads this rather than `isActivated`, which is only set after `start()` has left
    /// observer queries behind.
    private var hasRequestedActivation = false
    private var hasActivatedReadyWork = false
    private var foregroundOpportunityConsumed = false
    private var foregroundCatchUpTask: Task<Void, Never>?
    /// Persists the re-arm timestamp so the debounce survives an app relaunch. Injectable for tests.
    private let rearmStore: BackgroundSyncSettingsStore
    #if canImport(HealthKit)
    private let backgroundDeliveryCoordinator: HealthKitBackgroundDeliveryCoordinator
    #endif

    private lazy var engine = AutomaticSyncEngine(
        pendingStore: viewModel.automaticSyncSettingsStore,
        processType: { @MainActor [weak viewModel] typeCode, pendingGenerations in
            guard let viewModel else { return .blocked }
            return await viewModel.processAutomaticSyncType(
                typeCode,
                pendingGenerations: pendingGenerations
            )
        },
        performOpportunity: { @MainActor [weak viewModel] opportunity, processPendingTypes in
            guard let viewModel else { return }
            if opportunity.reason == .manualSync {
                await viewModel.performManualSyncOpportunity(
                    processPendingTypes: processPendingTypes
                )
                return
            }
            _ = await viewModel.performAutomaticSyncOpportunity(
                opportunity: opportunity,
                processPendingTypes: processPendingTypes
            )
        },
        startOwner: { @MainActor [weak self] cancelOwner in
            self?.beginOwner(cancelOwner: cancelOwner) ?? {}
        },
        deadlineRegistry: viewModel.observerDeadlineRegistry
    )

    init(
        viewModel: HealthBridgeCompanionViewModel,
        rearmStore: BackgroundSyncSettingsStore = BackgroundSyncSettingsStore()
    ) {
        self.viewModel = viewModel
        self.rearmStore = rearmStore
        #if canImport(HealthKit)
        backgroundDeliveryCoordinator = HealthKitBackgroundDeliveryCoordinator(
            deadlineRegistry: viewModel.observerDeadlineRegistry
        )
        #endif
    }

    func prepareForBackgroundLaunch() {
        guard viewModel.automaticSyncLaunchPreparationIsAllowed else { return }
        #if os(iOS)
        BackgroundURLSessionOutboxUploader.shared
            .setAutomaticContinuationAdmissionOpen(true)
        #endif
        activateObservers(allowBeforeBootstrap: true)
        BackgroundRefreshScheduler.scheduleNextRefreshIfNeeded(viewModel: viewModel)
    }

    func activateIfReady(scheduleOutbox: Bool = true) {
        guard viewModel.automaticSyncRuntimeIsReady else { return }
        #if os(iOS)
        BackgroundURLSessionOutboxUploader.shared
            .setAutomaticContinuationAdmissionOpen(true)
        #endif
        guard !hasActivatedReadyWork else { return }
        hasActivatedReadyWork = true
        if !isActivated {
            activateObservers(allowBeforeBootstrap: false)
        }
        if scheduleOutbox {
            viewModel.schedulePendingBackgroundOutboxUploadsIfAllowed()
        }
        BackgroundRefreshScheduler.scheduleNextRefreshIfNeeded(viewModel: viewModel)
        runForegroundCatchUpIfNeeded()
    }

    func stopAdmission() {
        #if os(iOS)
        BackgroundURLSessionOutboxUploader.shared
            .setAutomaticContinuationAdmissionOpen(false)
        #endif
        isActivated = false
        hasRequestedActivation = false
        hasActivatedReadyWork = false
        foregroundOpportunityConsumed = false
        foregroundCatchUpTask?.cancel()
        foregroundCatchUpTask = nil
        engine.cancelActiveOwner()
        BackgroundRefreshScheduler.cancelPendingRefresh()
        #if canImport(HealthKit)
        backgroundDeliveryCoordinator.stop(
            healthTypes: HealthBridgeBackgroundSync.allKnownBackgroundDeliveryHealthTypes
        )
        #endif
        viewModel.setAutomaticSyncActiveObserverCount(0)
    }

    func cancelAndWait() async {
        await engine.cancelAndWait()
    }

    func runForegroundCatchUpIfNeeded() {
        let opportunityWasConsumed = foregroundOpportunityConsumed
        guard !opportunityWasConsumed else { return }
        foregroundOpportunityConsumed = true
        reconcileRegistrations()
        if viewModel.usesMailboxTransport {
            viewModel.runForegroundMailboxReconciliationIfNeeded()
            return
        }
        guard viewModel.automaticSyncShouldRunForegroundCatchUp(
            opportunityWasConsumed: opportunityWasConsumed
        ),
              foregroundCatchUpTask == nil else {
            return
        }
        foregroundCatchUpTask = Task { @MainActor [weak self] in
            guard let self else { return }
            self.viewModel.publishAutomaticSyncForegroundCatchUpStarted()
            try? await self.engine.requestRun(reason: .launchCatchUp)
            self.foregroundCatchUpTask = nil
        }
    }

    func noteSceneLeftActive() {
        foregroundOpportunityConsumed = false
        viewModel.noteSceneLeftActive()
    }

    /// HealthKit stops launching a backgrounded app for a while, and a missed acknowledgement can
    /// stop it outright. Re-arm the observed types on foreground so registrations survive a long gap.
    /// Debounced: at most one re-arm per foreground session and never more than once per 10 minutes.
    /// The timestamp is persisted, so an app relaunch does not reset the window.
    func noteSceneBecameActive(now: Date = Date()) {
        guard isActivated else { return }
        #if canImport(HealthKit)
        guard HKHealthStore.isHealthDataAvailable() else { return }
        // Re-arming a coordinator with no observer query would enable `.immediate` delivery with
        // nothing to call the completion handler, which is exactly the wake-up health-relay #78
        // exists to prevent. `isActivated` already implies an observer now, but keep the explicit
        // check so a re-arm can never outrun the observer registration.
        guard backgroundDeliveryCoordinator.activeObserverCount > 0 else { return }
        guard viewModel.backgroundSyncEnabled else { return }
        guard BackgroundDeliveryRearmPolicy.admitsRearm(
            lastRearmAt: rearmStore.lastBackgroundDeliveryRearmAt(),
            now: now
        ) else { return }
        rearmStore.recordBackgroundDeliveryRearm(at: now)
        let healthTypes = viewModel.automaticSyncObserverHealthTypes()
        viewModel.noteBackgroundDeliveryRearmStarted(expectedTypeCount: healthTypes.count)
        backgroundDeliveryCoordinator.rearmBackgroundDelivery(
            healthTypes: healthTypes,
            registrationHandler: { [weak viewModel] typeCode, succeeded in
                viewModel?.noteBackgroundDeliveryRearmResult(typeCode: typeCode, succeeded: succeeded)
            }
        )
        #endif
    }

    func handleBackgroundRefresh() async {
        BackgroundRefreshScheduler.noteRequestConsumed()
        reconcileRegistrations()
        try? await engine.requestRun(
            reason: .scheduledRefresh,
            bootstrapBeforeRun: true
        )
        BackgroundRefreshScheduler.scheduleNextRefreshIfNeeded(viewModel: viewModel)
    }

    func runAutomaticSync(
        reason: AutomaticSyncReason,
        diagnosticRunID: UUID = UUID()
    ) async {
        try? await engine.requestRun(
            reason: reason,
            diagnosticRunID: diagnosticRunID
        )
    }

    func runManualSync() async {
        await engine.cancelAndWait()
        guard !Task.isCancelled else { return }
        try? await engine.requestRun(reason: .manualSync)
    }

    private func beginOwner(
        cancelOwner: @escaping AutomaticSyncEngine.CancelOwner
    ) -> AutomaticSyncEngine.FinishOwner {
        viewModel.setAutomaticSyncOwnerActive(true)
        #if os(iOS)
        let identifier = UIApplication.shared.beginBackgroundTask(
            withName: "HealthBridge automatic sync",
            expirationHandler: cancelOwner
        )
        return { @MainActor [weak self] in
            self?.viewModel.setAutomaticSyncOwnerActive(false)
            guard identifier != .invalid else { return }
            UIApplication.shared.endBackgroundTask(identifier)
        }
        #else
        return { @MainActor [weak self] in
            self?.viewModel.setAutomaticSyncOwnerActive(false)
        }
        #endif
    }

    private func reconcileRegistrations() {
        guard isActivated else { return }
        #if canImport(HealthKit)
        viewModel.prepareAutomaticSyncRegistrationReconciliation(
            expectedTypeCount: backgroundDeliveryCoordinator.activeObserverCount
        )
        backgroundDeliveryCoordinator.reconcileRegistrations()
        #endif
    }

    /// Starts the observer queries and marks the runtime activated only when the coordinator ended
    /// up with at least one. `start()` can return before registering anything, and a runtime that
    /// looked activated in that state would re-arm delivery with no observer behind it.
    private func activateObservers(allowBeforeBootstrap: Bool) {
        hasRequestedActivation = true
        startObservers(allowBeforeBootstrap: allowBeforeBootstrap)
        isActivated = hasActiveObserverQueries
    }

    private var hasActiveObserverQueries: Bool {
        #if canImport(HealthKit)
        return backgroundDeliveryCoordinator.activeObserverCount > 0
        #else
        return false
        #endif
    }

    private func startObservers(allowBeforeBootstrap: Bool) {
        #if canImport(HealthKit)
        guard HKHealthStore.isHealthDataAvailable() else {
            viewModel.publishHealthKitUnavailableForAutomaticSync()
            return
        }
        let healthTypes = viewModel.automaticSyncObserverHealthTypes()
        let expectedConnectionGeneration = viewModel.automaticSyncConnectionGeneration
        backgroundDeliveryCoordinator.start(
            healthTypes: healthTypes,
            registrationHandler: { [weak viewModel] typeCode, succeeded in
                viewModel?.noteHealthKitBackgroundDeliveryRegistration(
                    typeCode: typeCode,
                    succeeded: succeeded
                )
            },
            observerEntryHandler: viewModel.automaticSyncObserverEntryHandler(),
            isCurrent: { [weak self, weak viewModel] in
                guard let self, let viewModel, self.hasRequestedActivation else { return false }
                return viewModel.automaticSyncObserverIsCurrent(
                    expectedConnectionGeneration: expectedConnectionGeneration,
                    allowBeforeBootstrap: allowBeforeBootstrap
                )
            },
            observerAdmissionHandler: { [weak viewModel] typeCode, diagnosticRunID in
                guard let viewModel else { return .complete(nil) }
                return viewModel.admitAutomaticSyncObserver(
                    typeCode: typeCode,
                    diagnosticRunID: diagnosticRunID
                )
            },
            observerCompletionHandler: { [weak viewModel] completedDraft, latency in
                viewModel?.persistCompletedObserverAutomaticSyncDiagnostic(
                    completedDraft,
                    latency: latency
                )
            }
        ) { [weak self] typeCode, diagnosticRunID in
            self?.engine.requestRunWithoutWaiting(
                reason: .observer(typeCode: typeCode),
                diagnosticRunID: diagnosticRunID
            )
            return nil
        }
        let observerCount = backgroundDeliveryCoordinator.activeObserverCount
        viewModel.setAutomaticSyncActiveObserverCount(observerCount)
        viewModel.recordAutomaticSyncRegistrationStarted(
            expectedTypeCount: observerCount
        )
        #endif
    }
}
