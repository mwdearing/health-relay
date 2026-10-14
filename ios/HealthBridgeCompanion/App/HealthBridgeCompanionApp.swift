import SwiftUI
#if os(iOS)
import UIKit
#endif

@MainActor
final class HealthBridgeCompanionApplicationRuntime {
    static let shared = HealthBridgeCompanionApplicationRuntime()

    let viewModel: HealthBridgeCompanionViewModel
    let automaticSyncRuntime: AutomaticSyncRuntime
    private let backgroundLaunchPreparation: @MainActor () -> Void

    init(
        viewModel: HealthBridgeCompanionViewModel = HealthBridgeCompanionViewModel(),
        backgroundLaunchPreparation: (@MainActor () -> Void)? = nil
    ) {
        self.viewModel = viewModel
        let automaticSyncRuntime = AutomaticSyncRuntime(viewModel: viewModel)
        self.automaticSyncRuntime = automaticSyncRuntime
        viewModel.installAutomaticSyncRuntime(automaticSyncRuntime)
        self.backgroundLaunchPreparation = backgroundLaunchPreparation ?? {
            automaticSyncRuntime.prepareForBackgroundLaunch()
        }
    }

    func prepareForBackgroundLaunch() {
        backgroundLaunchPreparation()
    }

    func bootstrap() async {
        await viewModel.bootstrap()
    }

    func runForegroundCatchUpIfNeeded() {
        automaticSyncRuntime.runForegroundCatchUpIfNeeded()
    }

    func noteSceneLeftActive() {
        automaticSyncRuntime.noteSceneLeftActive()
    }

    func noteSceneBecameActive() {
        automaticSyncRuntime.noteSceneBecameActive()
    }

    func handleBackgroundRefresh() async {
        await automaticSyncRuntime.handleBackgroundRefresh()
    }
}

/// The live app: view model, lifecycle handling and pairing links. It is built only outside sample
/// launches, so a `-HealthRelaySampleScene` launch never creates the view model or runs bootstrap.
private struct LiveRootView: View {
    @Environment(\.scenePhase) private var scenePhase
    private let applicationRuntime = HealthBridgeCompanionApplicationRuntime.shared
    @StateObject private var viewModel = HealthBridgeCompanionApplicationRuntime.shared.viewModel

    var body: some View {
        ContentView(viewModel: viewModel)
            .onOpenURL { url in
                Task { await viewModel.importPairingURL(url) }
            }
            .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
                guard let url = activity.webpageURL else { return }
                Task { await viewModel.importPairingURL(url) }
            }
            .task {
                await applicationRuntime.bootstrap()
                guard !Task.isCancelled, scenePhase == .active else { return }
                applicationRuntime.noteSceneBecameActive()
                applicationRuntime.runForegroundCatchUpIfNeeded()
            }
            .onChange(of: scenePhase) { _, newPhase in
                if newPhase == .active {
                    Task { @MainActor in
                        await applicationRuntime.bootstrap()
                        guard !Task.isCancelled, scenePhase == .active else { return }
                        applicationRuntime.noteSceneBecameActive()
                        applicationRuntime.runForegroundCatchUpIfNeeded()
                    }
                } else {
                    applicationRuntime.noteSceneLeftActive()
                    if newPhase == .background {
                        viewModel.schedulePendingBackgroundOutboxUploadsIfAllowed()
                    }
                }
            }
    }
}

@main
@MainActor
struct HealthBridgeCompanionApp: App {
    /// Set by `-HealthRelaySampleScene <name>` (see SampleReviewLaunch). Nil for the live app.
    private let sampleScene = SampleReviewLaunch.requestedScene(arguments: ProcessInfo.processInfo.arguments)
    #if os(iOS)
    @UIApplicationDelegateAdaptor(HealthBridgeBackgroundURLSessionAppDelegate.self) private var backgroundURLSessionAppDelegate
    #endif

    /// Read lazily so a sample launch never constructs the live runtime.
    private var applicationRuntime: HealthBridgeCompanionApplicationRuntime {
        HealthBridgeCompanionApplicationRuntime.shared
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if let sampleScene {
                    NavigationStack {
                        SampleReviewView(scene: sampleScene)
                    }
                } else {
                    LiveRootView()
                }
            }
            .preferredColorScheme(.dark)
        }
        .backgroundTask(.appRefresh(HealthBridgeBackgroundSync.appRefreshIdentifier)) {
            guard sampleScene == nil else { return }
            await applicationRuntime.handleBackgroundRefresh()
        }
    }
}
