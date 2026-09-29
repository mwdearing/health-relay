import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var viewModel: HealthBridgeCompanionViewModel
    @State private var showPendingPairingCancellationConfirmation = false
    @State private var showExportFileImporter = false

    init(viewModel: HealthBridgeCompanionViewModel = HealthBridgeCompanionViewModel()) {
        _viewModel = StateObject(wrappedValue: viewModel)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: HealthBridgeSpacing.section) {
                    intro
                    statusCard

                    if viewModel.hasPendingPairing {
                        pairingRecoveryCard
                    }

                    if viewModel.setupState == .unpaired {
                        pairingCard
                    } else if !viewModel.healthPermissionsRequested {
                        healthAccessCard
                    } else {
                        syncControlCard
                        exportImportCard
                    }
                }
                .padding(.horizontal, HealthBridgeSpacing.screen)
                .padding(.top, 8)
                .padding(.bottom, 16)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("HealthRelay")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        AppDetailsView(viewModel: viewModel)
                    } label: {
                        Image(systemName: "gearshape")
                    }
                    // The toolbar button stays neutral: the root accent tints its glass in dark mode.
                    .tint(Color.primary)
                    .accessibilityLabel("Settings")
                }
            }
            .safeAreaInset(edge: .bottom) {
                if showsSyncControls {
                    syncBar
                }
            }
        }
        // Apply the brand accent explicitly. Reading the asset by name is what the other
        // colors already do, and it does not depend on the global accent plumbing.
        .tint(.relayAccent)
        .fileImporter(
            isPresented: $showExportFileImporter,
            allowedContentTypes: [.zip],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case let .success(urls):
                if let url = urls.first {
                    viewModel.parseAppleHealthExport(from: url)
                }
            case let .failure(error):
                viewModel.reportAppleHealthExportPickerFailure(error)
            }
        }
        .sheet(isPresented: Binding(
            get: { viewModel.pendingExportImportSummary != nil },
            set: { isPresented in
                if !isPresented {
                    viewModel.cancelPendingAppleHealthExportImport()
                }
            }
        )) {
            if let summary = viewModel.pendingExportImportSummary {
                ExportImportReviewView(
                    viewModel: viewModel,
                    summary: summary,
                    onCancel: { viewModel.cancelPendingAppleHealthExportImport() }
                )
            }
        }
    }

    /// The Sync card only appears once the iPhone is paired and Health access was requested.
    private var showsSyncControls: Bool {
        viewModel.setupState != .unpaired && viewModel.healthPermissionsRequested
    }

    private var intro: some View {
        Text("Sync Apple Health data from this iPhone to your own local server.")
            .font(.subheadline)
            .foregroundStyle(.relaySecondaryText)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var statusCard: some View {
        HStack(alignment: .center, spacing: 14) {
            statusGlyph
            VStack(alignment: .leading, spacing: 4) {
                Text(statusTitle)
                    .font(.title3.weight(.semibold))
                Text(statusSubtitle)
                    .font(.subheadline)
                    .foregroundStyle(.relaySecondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                if let lastSyncedAt = viewModel.lastSuccessfulSyncAt {
                    // Re-evaluated every minute so the relative time does not go stale.
                    TimelineView(.everyMinute) { context in
                        if let line = LastSyncedFormatter.line(lastSyncedAt: lastSyncedAt, now: context.date) {
                            Text(line)
                                .font(.footnote)
                                .foregroundStyle(.relaySecondaryText)
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .cardStyle()
    }

    private var pairingCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Connect Your Server",
                subtitle: "Scan the private QR with iPhone Camera, open its setup link, or paste it here. After pairing, the secret key stays on this iPhone."
            )

            VStack(alignment: .leading, spacing: 12) {
                Label("Direct / Tailscale-compatible", systemImage: "network")
                    .font(.headline)
                Text("Recommended on Linux and Mac. The receiver setup invitation selects this transport by default.")
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)

                Label("Encrypted iCloud Mailbox (Beta)", systemImage: "lock.icloud.fill")
                    .font(.headline)
                Text("No VPN required • Mac only • Best-effort, eventual delivery")
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)

                Text("Custom HTTPS remains a Direct connection. Local same-network-only setup is Advanced / Limited.")
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)
            }
            .padding(14)
            .background(Color(.tertiarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))

            TextField("Paste private setup link", text: $viewModel.pairingImportText, axis: .vertical)
                .lineLimit(2...4)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.body)
                .padding(14)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))

            PrimaryButton(
                title: "Connect",
                subtitle: "Redeem this one-time invitation",
                systemImage: "link.badge.plus",
                isDisabled: !viewModel.canImportPairingText,
                isLoading: viewModel.isPairing
            ) {
                Task { await viewModel.importPairingText() }
            }

            Divider()

            DisclosureGroup {
                manualPairingFields
                    .padding(.top, 12)
            } label: {
                Text("Use a code instead")
                    .font(.headline)
            }
        }
        .cardStyle()
    }

    private var pairingRecoveryCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Pairing Recovery Pending", systemImage: "exclamationmark.shield.fill")
                .font(.headline)
            Text(
                viewModel.mailboxKeyDiagnosticState == .lost
                    ? "Automatic sync is paused because the mailbox connection key is lost. Clear this pending pairing before using the dedicated key recovery action."
                    : "Automatic sync is paused. Retry the saved attempt after fixing the route, or clear it before opening a different setup link."
            )
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)
            if let pairingFailure = viewModel.pairingFailurePresentation {
                InlineNotice(
                    message: pairingFailure.message,
                    systemImage: "exclamationmark.triangle.fill",
                    tint: .relayWaitingInk
                )
            }
            if viewModel.mailboxKeyDiagnosticState != .lost {
                Button("Retry Pairing") {
                    Task {
                        await viewModel.retryPendingPairing()
                    }
                }
                .disabled(viewModel.isPairing)
            }
            Button("Clear Pending Pairing and Disconnect", role: .destructive) {
                showPendingPairingCancellationConfirmation = true
            }
            .disabled(viewModel.isPairing)
        }
        .cardStyle()
        .confirmationDialog(
            "Clear pending pairing and disconnect?",
            isPresented: $showPendingPairingCancellationConfirmation,
            titleVisibility: .visible
        ) {
            Button("Clear Pending Pairing and Disconnect", role: .destructive) {
                Task {
                    await viewModel.cancelPendingPairing()
                }
            }
            Button("Keep Pending Pairing", role: .cancel) {}
        } message: {
            Text("This removes the pending invitation and also removes the currently saved connection. Apple Health data is not deleted.")
        }
    }

    private var manualPairingFields: some View {
        VStack(alignment: .leading, spacing: 14) {
            TextField("Server address", text: $viewModel.manualPairingServer)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .padding(12)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))

            TextField("Invitation code", text: $viewModel.manualPairingCode)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                .keyboardType(.asciiCapable)
                .padding(12)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))

            PrimaryButton(
                title: "Connect with Code",
                subtitle: "Codes expire and work only once",
                systemImage: "number.square.fill",
                isDisabled: !viewModel.canRedeemManualPairing,
                isLoading: viewModel.isPairing
            ) {
                Task { await viewModel.redeemManualPairing() }
            }
        }
    }

    private var healthAccessCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Apple Health Access",
                subtitle: "Use Apple Health's permission screen to choose what this iPhone can share."
            )

            Text("HealthRelay requests read-only access for every supported type currently available on this iPhone. Choose exactly what to allow in Apple Health.")
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)
                .fixedSize(horizontal: false, vertical: true)

            Text(viewModel.healthPermissionScopeSummary)
                .font(.caption)
                .foregroundStyle(.relaySecondaryText)
                .fixedSize(horizontal: false, vertical: true)

            PrimaryButton(
                title: "Allow Health Access",
                subtitle: "Opens Apple Health permission sheet",
                systemImage: "checkmark.shield.fill",
                isDisabled: false,
                isLoading: viewModel.isRequestingHealthPermissions
            ) {
                Task { await viewModel.requestHealthPermissions() }
            }

            if !viewModel.healthPermissionNotice.isEmpty {
                InlineNotice(
                    message: viewModel.healthPermissionNotice,
                    systemImage: viewModel.healthPermissionNoticeIsError ? "exclamationmark.triangle.fill" : "info.circle.fill",
                    tint: viewModel.healthPermissionNoticeIsError ? .relayWaitingInk : .relayAccentInk
                )
            }
        }
        .cardStyle()
    }

    private var syncControlCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Sync",
                subtitle: "Send allowed Apple Health data to your local server."
            )

            CompactSettingRow(
                title: "Sync Range",
                systemImage: "clock.arrow.circlepath",
                tint: .relayAccentInk
            ) {
                Picker("Sync Range", selection: Binding(
                    get: { viewModel.healthHistoryDepthOptionID },
                    set: { viewModel.setHealthHistoryDepthOption($0) }
                )) {
                    ForEach(viewModel.healthHistoryDepthRows) { row in
                        Text(row.title).tag(row.id)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
            }

            Divider()

            Toggle(isOn: Binding(
                get: { viewModel.automaticSyncToggleIsOn },
                set: { enabled in
                    viewModel.requestBackgroundSyncEnabled(enabled)
                }
            )) {
                SettingRowLabel(
                    title: "Automatic Sync",
                    subtitle: viewModel.automaticSyncScopeSummary,
                    systemImage: "arrow.triangle.2.circlepath",
                    tint: .relayAccentInk
                )
            }
            .disabled(!viewModel.canChangeAutomaticSyncSetting)

            Text("If nothing syncs, check that HealthRelay is allowed to read your data: Health app > profile picture > Privacy > Apps > HealthRelay.")
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .cardStyle()
    }

    /// Primary actions sit at the bottom of the screen, above the home indicator.
    private var syncBar: some View {
        VStack(spacing: 8) {
            PrimaryButton(
                title: "Sync Now",
                subtitle: syncActionSubtitle,
                systemImage: viewModel.syncPresentationIsActive ? "arrow.triangle.2.circlepath" : "arrow.up.arrow.down.circle.fill",
                isDisabled: !viewModel.canRunPrimaryAction,
                isLoading: viewModel.syncPresentationIsActive
            ) {
                Task { await viewModel.performPrimaryAction() }
            }

            // Gated on the same flag the "Sync Now" spinner/disabled state uses
            // (syncPresentationIsActive = isSyncing || automaticSyncOwnerIsActive),
            // not just isSyncing alone. A HealthKit-observer-driven Automatic Sync
            // run sets only automaticSyncOwnerIsActive, never isSyncing -- gating on
            // isSyncing meant Cancel never even appeared during that kind of run
            // (Michael, 2026-09-29: "Cancel button never shows up in auto"), leaving
            // no way to interrupt it short of disabling Automatic Sync entirely.
            if viewModel.syncPresentationIsActive {
                PrimaryButton(
                    title: "Cancel",
                    subtitle: "Stop this sync. Already queued uploads are kept.",
                    systemImage: "xmark.circle.fill",
                    emphasis: .caution,
                    isDisabled: false
                ) {
                    Task { await viewModel.cancelCurrentForegroundAction() }
                }
            }
        }
        .padding(.horizontal, HealthBridgeSpacing.screen)
        .padding(.bottom, 8)
    }

    /// HealthRelay addition: a manual, one-shot import of an Apple Health export.zip
    /// for lab results only -- ECG and medications already sync live above and never
    /// need this. The file is parsed entirely on-device; nothing is sent until the
    /// review sheet's Send button is tapped.
    private var exportImportCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeader(
                title: "Import Health Export",
                subtitle: "Add lab results from an Apple Health export.zip file."
            )

            // A rare, secondary action: a quiet capsule, so Sync Now stays the only mint button.
            Button {
                showExportFileImporter = true
            } label: {
                Label("Choose Export File", systemImage: "doc.zipper")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            }
            .relaySecondaryButtonStyle()
            .controlSize(.large)
            .disabled(!viewModel.canRunPrimaryAction || viewModel.isReadingExport)

            // Only explain the disabled button when a sync is the reason; not while unpaired.
            if viewModel.syncPresentationIsActive && !viewModel.isReadingExport {
                Text("Available when sync finishes.")
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)
            }

            if viewModel.isReadingExport {
                HStack(spacing: 12) {
                    ProgressView()
                    Text("Reading export\u{2026}")
                        .font(.footnote)
                        .foregroundStyle(.relaySecondaryText)
                    Spacer(minLength: 8)
                    Button("Cancel") {
                        viewModel.cancelAppleHealthExportRead()
                    }
                    .font(.footnote.weight(.semibold))
                }
                .accessibilityElement(children: .contain)
            }

            if let notice = viewModel.exportSendNotice {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Label(notice, systemImage: "checkmark.circle.fill")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.relaySecondaryText)
                    Spacer(minLength: 8)
                    Button("Dismiss") {
                        viewModel.dismissExportSendNotice()
                    }
                    .font(.footnote)
                }
            }

            // Visible, not just a VoiceOver hint: people need to know where the file comes from.
            Text("In the Health app: profile picture \u{2192} Export All Health Data.")
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .cardStyle()
    }

    private var statusGlyph: some View {
        Image(systemName: statusSymbol)
            .font(.system(size: 22, weight: .bold))
            .foregroundStyle(statusTone.ink)
            .frame(width: 52, height: 52)
            .background(statusTone.tint, in: Circle())
            .overlay(Circle().strokeBorder(statusTone.ink.opacity(0.25), lineWidth: 1))
            .accessibilityHidden(true)
    }

    private var statusTitle: String {
        if viewModel.hasPendingPrivateStorageRecovery { return "Recovery Required" }
        if !viewModel.canSendConnectionTest && viewModel.pendingOutboxCount > 0 { return "Queued Uploads Waiting" }
        if viewModel.syncPresentationIsActive && viewModel.canSendConnectionTest { return "Syncing" }
        if viewModel.statusIsError { return syncErrorTitle }
        if !viewModel.canSendConnectionTest { return "Not Connected" }
        if viewModel.pendingOutboxCount > 0 { return "Waiting to Send" }
        if viewModel.backgroundSyncEnabled { return "Ready to Sync" }
        if viewModel.healthPermissionsRequested { return "Ready to Sync" }
        return "Allow Health Access"
    }

    private var statusSubtitle: String {
        if viewModel.hasPendingPrivateStorageRecovery {
            return "Open Settings, reset private sync state, then pair this iPhone again."
        }
        if !viewModel.canSendConnectionTest && viewModel.pendingOutboxCount > 0 {
            return "Queued uploads remain on this iPhone. Reconnect from setup link to retry them."
        }
        if viewModel.syncPresentationIsActive && viewModel.canSendConnectionTest {
            return "Updating allowed Apple Health data."
        }
        if viewModel.statusIsError { return userFacingStatusMessage }
        if !viewModel.canSendConnectionTest { return "Connect this iPhone before syncing." }
        if viewModel.setupState == .pairedNeedsHealthPermission { return "Open Apple Health and allow the data you want to sync." }
        if viewModel.pendingOutboxCount > 0 {
            return CompanionCopy.pendingUploadsSentence(
                count: viewModel.pendingOutboxCount,
                usesMailbox: viewModel.usesMailboxTransport
            )
        }
        if viewModel.backgroundSyncEnabled { return viewModel.automaticSyncScopeSummary }
        return "Use Sync Now to update your allowed Apple Health data."
    }

    private var syncErrorTitle: String {
        let message = viewModel.statusMessage.lowercased()
        if message.contains("sync") || message.contains("queued upload") || message.contains("outbox") {
            return "Sync Failed"
        }
        if message.contains("connection") || message.contains("bridge") || message.contains("server") || message.contains("receiver") {
            return "Connection Failed"
        }
        return "Needs Attention"
    }

    private var statusTone: StatusTone {
        if !viewModel.canSendConnectionTest && viewModel.pendingOutboxCount > 0 { return .waiting }
        if !viewModel.canSendConnectionTest { return .failed }
        if viewModel.syncPresentationIsActive { return .ready }
        if viewModel.statusIsError { return .failed }
        if viewModel.pendingOutboxCount > 0 { return .waiting }
        if viewModel.backgroundSyncEnabled || viewModel.healthPermissionsRequested { return .ready }
        return .waiting
    }

    private var statusSymbol: String {
        if !viewModel.canSendConnectionTest && viewModel.pendingOutboxCount > 0 { return "exclamationmark" }
        if !viewModel.canSendConnectionTest { return "xmark" }
        if viewModel.syncPresentationIsActive { return "arrow.triangle.2.circlepath" }
        if viewModel.statusIsError || viewModel.pendingOutboxCount > 0 { return "exclamationmark" }
        if viewModel.healthPermissionsRequested || viewModel.backgroundSyncEnabled { return "checkmark" }
        return "heart.text.square"
    }

    private var syncActionSubtitle: String {
        if viewModel.setupState == .pairedNeedsHealthPermission {
            return "Choose data in Apple Health first"
        }
        if viewModel.setupState == .degraded {
            return "Run a manual update"
        }
        return "Send allowed Health data to your server"
    }

    private var userFacingStatusMessage: String {
        CompanionPrimaryStatusMessage.sanitized(
            from: viewModel.statusMessage,
            isError: viewModel.statusIsError
        )
    }
}

private struct ReceiverSettingsView: View {
    @ObservedObject var viewModel: HealthBridgeCompanionViewModel
    @State private var showDisconnectConfirmation = false
    @State private var showDisconnectFailureAlert = false
    @State private var disconnectFailureMessage = ""
    @State private var showClearQueuedUploadsConfirmation = false
    @State private var showMailboxKeyResetConfirmation = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: HealthBridgeSpacing.section) {
                SectionHeader(
                    title: "Connection",
                    subtitle: "Use the private setup link from your computer. Manual setup is only a fallback."
                )
                .padding(.horizontal, HealthBridgeSpacing.screen)

                connectionStatusCard
                    .padding(.horizontal, HealthBridgeSpacing.screen)

                setupLinkCard
                    .padding(.horizontal, HealthBridgeSpacing.screen)

                if !viewModel.receiverSettingsSaved {
                    manualSettingsCard
                        .padding(.horizontal, HealthBridgeSpacing.screen)
                }

                if showsDangerZone {
                    dangerZoneCard
                        .padding(.horizontal, HealthBridgeSpacing.screen)
                }
            }
            .padding(.top, 24)
            .padding(.bottom, 40)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Connection")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("Disconnect from server?", isPresented: $showDisconnectConfirmation, titleVisibility: .visible) {
            Button("Disconnect from Server", role: .destructive) {
                Task {
                    let outcome = await viewModel.disconnectReceiver()
                    switch outcome {
                    case .disconnected:
                        break
                    case .rejected(let message, let pendingOutboxCount, let connectionPreserved):
                        if connectionPreserved, let pendingOutboxCount, pendingOutboxCount > 0 {
                            disconnectFailureMessage = "Queued uploads are waiting on this iPhone. Bring the server back and tap Sync Now to send them, or use Reset Private Sync State in Settings to discard them and rebuild local sync history before disconnecting."
                        } else if connectionPreserved, pendingOutboxCount == nil {
                            disconnectFailureMessage = "HealthRelay couldn’t verify whether queued uploads remain. Your saved server connection was not removed. Reopen Settings and try again after private storage is available."
                        } else {
                            disconnectFailureMessage = CompanionPrimaryStatusMessage.sanitized(
                                from: message,
                                isError: true
                            )
                        }
                        showDisconnectFailureAlert = true
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes the saved server connection from this iPhone. It does not delete Apple Health data.")
        }
        .alert("Can’t Disconnect Yet", isPresented: $showDisconnectFailureAlert) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(disconnectFailureMessage)
        }
        .confirmationDialog("Reset private sync state?", isPresented: $showClearQueuedUploadsConfirmation, titleVisibility: .visible) {
            Button("Reset Private Sync State", role: .destructive) {
                Task { await viewModel.clearPendingOutbox() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This permanently deletes unsent local Health payloads and resets local sync cursors so the next connection rebuilds receiver history. If the saved connection is unreadable, it is also removed. Apple Health data itself is not deleted.")
        }
        .confirmationDialog(
            "Reset lost mailbox connection key?",
            isPresented: $showMailboxKeyResetConfirmation,
            titleVisibility: .visible
        ) {
            Button("Reset Lost Mailbox Connection Key", role: .destructive) {
                Task { await viewModel.resetLostMailboxConnectionKey() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This resets unreadable mailbox connection key material and pending/saved pairing state only. It does not delete Apple Health data or permissions. Queued uploads and HealthKit sync cursors are not deleted.")
        }
    }

    private var connectionIsReachable: Bool {
        let message = viewModel.statusMessage.lowercased()
        return !viewModel.statusIsError
            && (message.contains("connection check passed")
                || message.contains("server connected")
                || message.contains("local bridge verified")
                || message.contains("connected to local bridge"))
    }

    private var mailboxFolderIsReady: Bool {
        !viewModel.statusIsError
            && viewModel.usesMailboxTransport
            && viewModel.statusMessage.contains("Mailbox folder is ready")
    }

    private var connectionNotice: String {
        let rawMessage = viewModel.statusMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        let message = rawMessage.lowercased()
        guard !rawMessage.isEmpty else { return "" }
        guard !message.contains("permission"), !message.contains("apple health") else { return "" }
        guard message.contains("connection")
            || message.contains("receiver")
            || message.contains("bridge url")
            || message.contains("local bridge")
            || message.contains("your server")
            || message.contains("setup link")
            || message.contains("disconnect")
            || message.contains("queued upload")
            || message.contains("private sync")
            || message.contains("mailbox")
        else {
            return "" }
        return CompanionPrimaryStatusMessage.sanitized(
            from: rawMessage,
            isError: viewModel.statusIsError
        )
    }

    private var connectionStatusCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(viewModel.canSendConnectionTest ? (mailboxFolderIsReady ? "Mailbox Folder Ready" : (connectionIsReachable ? "Server Reachable" : "Connection Saved")) : "Not Connected")
                        .font(.headline)
                    Text(viewModel.canSendConnectionTest ? (mailboxFolderIsReady ? "Receiver delivery not verified." : (connectionIsReachable ? "Ready to sync." : "Saved on this iPhone.")) : "Use a setup link to connect.")
                        .font(.caption)
                        .foregroundStyle(.relaySecondaryText)
                }
            } icon: {
                Image(systemName: viewModel.canSendConnectionTest ? (connectionIsReachable ? "checkmark.circle.fill" : "link.circle.fill") : "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(viewModel.canSendConnectionTest ? (connectionIsReachable ? .relayReadyInk : .relayAccentInk) : .relayFailedInk)
            }

            Divider()

            LabeledContent("Mailbox connection key") {
                Text(viewModel.mailboxKeyLifecycleLabel)
                    .foregroundStyle(
                        viewModel.mailboxKeyDiagnosticState == .active ? .relayReadyInk : .relaySecondaryText
                    )
            }
            Text(viewModel.mailboxKeyLifecycleDetail)
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)

            if viewModel.mailboxKeyDiagnosticState == .lost {
                Button(role: .destructive) {
                    showMailboxKeyResetConfirmation = true
                } label: {
                    Label("Reset Lost Mailbox Connection Key", systemImage: "key.slash")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .disabled(!viewModel.canResetLostMailboxConnectionKey)
            }

            PrimaryButton(
                title: viewModel.usesMailboxTransport ? "Check Mailbox Folder" : "Check Connection",
                subtitle: viewModel.usesMailboxTransport ? "Verify the encrypted iCloud folder is available" : "Verify your server is reachable",
                systemImage: "wifi",
                isDisabled: !viewModel.canSendConnectionTest,
                isLoading: viewModel.isCheckingConnection
            ) {
                Task { await viewModel.checkConnection() }
            }

            if !connectionNotice.isEmpty {
                Label(connectionNotice, systemImage: viewModel.statusIsError ? "exclamationmark.triangle.fill" : "info.circle.fill")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(viewModel.statusIsError ? .relayWaitingInk : .relaySecondaryText)
                    .padding(.top, 2)
            }

            if viewModel.hasTransientPrivateStorageFailure {
                Button {
                    Task { await viewModel.retryPrivateStorage() }
                } label: {
                    Label("Retry Private Storage", systemImage: "arrow.clockwise")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .cardStyle()
    }

    private var dangerZoneCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Disconnect and Reset")
                .font(.headline)
            Text("These actions change or remove this iPhone's saved connection. Apple Health data is never deleted.")
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)
            if viewModel.canSendConnectionTest {
                Button(role: .destructive) {
                    showDisconnectConfirmation = true
                } label: {
                    Label("Disconnect from Server", systemImage: "rectangle.portrait.and.arrow.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
            if viewModel.pendingOutboxCount > 0
                || viewModel.hasPendingSleepTransition
                || viewModel.hasPendingOutboxDeletion
                || viewModel.hasPendingPrivateStorageRecovery
                || viewModel.hasPendingPairing {
                Button(role: .destructive) {
                    showClearQueuedUploadsConfirmation = true
                } label: {
                    Label(
                        viewModel.hasPendingPrivateStorageRecovery
                            ? "Reset Unreadable Private Sync State"
                            : viewModel.hasPendingOutboxDeletion
                            ? "Finish Resetting Private Sync State"
                            : viewModel.pendingOutboxCount > 0
                                ? "Reset Private Sync State (\(viewModel.pendingOutboxCount) queued)"
                                : "Reset Private Sync State",
                        systemImage: "trash"
                    )
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
        }
        .cardStyle()
    }

    private var showsDangerZone: Bool {
        viewModel.canSendConnectionTest
            || viewModel.pendingOutboxCount > 0
            || viewModel.hasPendingSleepTransition
            || viewModel.hasPendingOutboxDeletion
            || viewModel.hasPendingPrivateStorageRecovery
            || viewModel.hasPendingPairing
    }

    private var setupLinkCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader(
                title: viewModel.canSendConnectionTest ? "Replace Connection" : "Setup Link",
                subtitle: viewModel.canSendConnectionTest ? "Paste a new setup link only if you want to replace this iPhone's saved connection." : "Setup links contain private connection details. Only paste them here. Do not share them in chat or screenshots."
            )
            TextField("Paste private setup link", text: $viewModel.pairingImportText, axis: .vertical)
                .lineLimit(2...4)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.body)
                .padding(14)
                .background(Color(.tertiarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))
            PrimaryButton(
                title: viewModel.canSendConnectionTest ? "Replace Connection" : "Connect from Setup Link",
                subtitle: "Save connection details securely on this iPhone",
                systemImage: "link.badge.plus",
                isDisabled: !viewModel.canImportPairingText,
                isLoading: viewModel.isPairing
            ) {
                Task { await viewModel.importPairingText() }
            }
        }
        .cardStyle()
    }

    private var manualSettingsCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionHeader(
                title: "Invitation Code",
                subtitle: "If you cannot open the setup link, enter the server address and one-time code shown on the setup page."
            )

            DisclosureGroup {
                VStack(alignment: .leading, spacing: 14) {
                    TextField("Server address", text: $viewModel.manualPairingServer)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .padding(12)
                        .background(Color(.tertiarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))
                    TextField("Invitation code", text: $viewModel.manualPairingCode)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .keyboardType(.asciiCapable)
                        .padding(12)
                        .background(Color(.tertiarySystemGroupedBackground))
                        .clipShape(RoundedRectangle(cornerRadius: HealthBridgeRadius.inset, style: .continuous))
                    PrimaryButton(
                        title: "Connect with Code",
                        subtitle: "Codes expire and work only once",
                        systemImage: "number.square.fill",
                        isDisabled: !viewModel.canRedeemManualPairing,
                        isLoading: viewModel.isPairing
                    ) {
                        Task { await viewModel.redeemManualPairing() }
                    }
                }
                .padding(.top, 12)
            } label: {
                Text("Use a code instead")
                    .font(.headline)
            }
        }
        .cardStyle()
    }
}

private struct AppDetailsView: View {
    @ObservedObject var viewModel: HealthBridgeCompanionViewModel

    private static let privacyPolicyURL = URL(string: "https://github.com/mwdearing/health-relay/blob/main/PRIVACY.md")!
    private static let supportURL = URL(string: "https://github.com/mwdearing/health-relay/issues")!

    var body: some View {
        List {
            Section("Connection") {
                NavigationLink {
                    ReceiverSettingsView(viewModel: viewModel)
                } label: {
                    Label(viewModel.canSendConnectionTest ? "Connection Saved" : "Set Up Connection", systemImage: "network")
                }
            }

            Section("Health Permissions") {
                Text("Apple Health can ask again when supported types become newly available on this iPhone. To review or change what HealthRelay can read, open the Health app > profile picture > Privacy > Apps > HealthRelay.")
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)
                Button("Request Health Access Again") {
                    Task { await viewModel.requestHealthPermissions() }
                }
                .disabled(viewModel.isRequestingHealthPermissions)
            }

            Section("Automatic Sync") {
                Text(viewModel.automaticSyncCoverageDetail)
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)
                LabeledContent("Current status", value: viewModel.backgroundSyncStatus)
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)
                NavigationLink {
                    DiagnosticsView(viewModel: viewModel)
                } label: {
                    Label("Diagnostics", systemImage: "stethoscope")
                }
            }

            Section("Activity Log") {
                NavigationLink {
                    ActivityLogView(viewModel: viewModel)
                } label: {
                    Label("View Logs", systemImage: "list.bullet.rectangle")
                }
                .disabled(viewModel.activityLogMessages.isEmpty)
            }

            Section("About") {
                LabeledContent("Version", value: appVersion)
                Link("Privacy Policy", destination: Self.privacyPolicyURL)
                Link("Support", destination: Self.supportURL)
            }
        }
        .navigationTitle("Settings")
    }

    private var appVersion: String {
        CompanionCopy.versionLine(
            shortVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
            build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        )
    }
}

private struct DiagnosticsView: View {
    @ObservedObject var viewModel: HealthBridgeCompanionViewModel

    var body: some View {
        List {
            Section {
                Group {
                    LabeledContent("Registration", value: viewModel.automaticSyncRegistrationLine)
                    LabeledContent("BG request", value: viewModel.automaticSyncScheduleLine)
                    LabeledContent("Last wake", value: viewModel.automaticSyncWakeLine)
                    LabeledContent("Last run", value: viewModel.automaticSyncRunLine)
                    LabeledContent("Latest lane", value: viewModel.automaticSyncLaneDiagnosticLine)
                }
                .font(.footnote)
                .foregroundStyle(.relaySecondaryText)
                if !viewModel.mailboxDeliveryDiagnosticLine.isEmpty {
                    Text(viewModel.mailboxDeliveryDiagnosticLine)
                        .font(.footnote)
                        .foregroundStyle(.relaySecondaryText)
                }
            } footer: {
                Text("Technical details for troubleshooting. They contain no health values.")
            }
        }
        .navigationTitle("Diagnostics")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct ActivityLogView: View {
    @ObservedObject var viewModel: HealthBridgeCompanionViewModel

    var body: some View {
        List {
            if viewModel.activityLogMessages.isEmpty {
                Text("No recent activity yet.")
                    .font(.footnote)
                    .foregroundStyle(.relaySecondaryText)
            } else {
                ForEach(ActivityLogMerger.identifiedRows(from: viewModel.activityLogMessages)) { row in
                    Text(row.text)
                        .font(.footnote)
                        .textSelection(.enabled)
                }
            }
        }
        .navigationTitle("Activity Log")
    }
}

private struct SettingRow<Accessory: View>: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let tint: Color
    @ViewBuilder let accessory: () -> Accessory

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            SettingRowLabel(title: title, subtitle: subtitle, systemImage: systemImage, tint: tint)
            Spacer(minLength: 8)
            accessory()
        }
    }
}

private struct CompactSettingRow<Accessory: View>: View {
    let title: String
    let systemImage: String
    let tint: Color
    @ViewBuilder let accessory: () -> Accessory

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: systemImage)
                .font(.headline)
                .foregroundStyle(tint)
                .frame(width: 28, height: 28)
            Text(title)
                .font(.headline)
            Spacer(minLength: 8)
            accessory()
        }
    }
}

private struct SettingRowLabel: View {
    let title: String
    let subtitle: String
    let systemImage: String
    let tint: Color

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .font(.headline)
                .foregroundStyle(tint)
                .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.relaySecondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

private struct SectionHeader: View {
    let title: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.headline)
            if !subtitle.isEmpty {
                Text(subtitle)
                    .font(.subheadline)
                    .foregroundStyle(.relaySecondaryText)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct PrimaryButton: View {
    enum Emphasis {
        case primary
        case caution
    }

    let title: String
    let subtitle: String
    let systemImage: String
    var emphasis: Emphasis = .primary
    let isDisabled: Bool
    var isLoading = false
    let action: () -> Void

    private var fill: Color {
        emphasis == .primary ? .relayMint : .relayWaitingTint
    }

    private var labelColor: Color {
        emphasis == .primary ? .relayOnMint : .relayWaitingInk
    }

    var body: some View {
        Button(action: action) {
            Label {
                Text(title)
                    .font(.headline)
            } icon: {
                if isLoading {
                    ProgressView()
                        .tint(labelColor)
                } else {
                    Image(systemName: systemImage)
                }
            }
            .foregroundStyle(labelColor)
            .frame(maxWidth: .infinity)
        }
        .relayProminentButtonStyle()
        .controlSize(.large)
        .tint(fill)
        // No extra fade: the system already dims a disabled button, and stacking a second
        // fade made the Connect button nearly invisible in dark mode.
        .disabled(isDisabled || isLoading)
        .accessibilityHint(subtitle)
    }
}

private struct InlineNotice: View {
    let message: String
    let systemImage: String
    let tint: Color

    var body: some View {
        Label(message, systemImage: systemImage)
            .font(.footnote)
            .foregroundStyle(tint)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private struct RowDivider: View {
    var body: some View {
        Divider().padding(.leading, 66)
    }
}

// Pastel palette drawn from the app icon (Assets.xcassets). Every fill pairs with an
// ink chosen for at least 4.5:1: deep ink on light pastels in light mode, pastel ink on
// deep tints in dark mode. Mint is the one primary-action fill.
private extension ShapeStyle where Self == Color {
    static var relayAccent: Color { Color("AccentColor") }
    static var relayMint: Color { Color("RelayMint") }
    static var relayOnMint: Color { Color("RelayOnMint") }
    static var relayAccentInk: Color { Color("RelayAccentInk") }
    static var relayReadyTint: Color { Color("RelayReadyTint") }
    static var relayReadyInk: Color { Color("RelayReadyInk") }
    static var relayWaitingTint: Color { Color("RelayWaitingTint") }
    static var relayWaitingInk: Color { Color("RelayWaitingInk") }
    static var relayFailedTint: Color { Color("RelayFailedTint") }
    static var relayFailedInk: Color { Color("RelayFailedInk") }
    static var relaySecondaryText: Color { Color("RelaySecondaryText") }
}

/// Status is the only place green, amber and rose appear.
private enum StatusTone {
    case ready
    case waiting
    case failed

    var tint: Color {
        switch self {
        case .ready: .relayReadyTint
        case .waiting: .relayWaitingTint
        case .failed: .relayFailedTint
        }
    }

    var ink: Color {
        switch self {
        case .ready: .relayReadyInk
        case .waiting: .relayWaitingInk
        case .failed: .relayFailedInk
        }
    }
}

private enum HealthBridgeSpacing {
    static let screen: CGFloat = 20
    static let section: CGFloat = 22
}

/// Cards and the shapes nested in them use one pair of radii, so corners stay concentric.
private enum HealthBridgeRadius {
    static let card: CGFloat = 26
    static let inset: CGFloat = 12
}

private extension View {
    /// Liquid Glass capsule on iOS 26 and later; a prominent capsule before that.
    @ViewBuilder
    func relayProminentButtonStyle() -> some View {
        if #available(iOS 26.0, *) {
            buttonStyle(.glassProminent)
        } else {
            buttonStyle(.borderedProminent)
                .buttonBorderShape(.capsule)
        }
    }

    /// Quiet capsule for secondary actions: glass on iOS 26 and later, bordered before that.
    @ViewBuilder
    func relaySecondaryButtonStyle() -> some View {
        if #available(iOS 26.0, *) {
            buttonStyle(.glass)
        } else {
            buttonStyle(.bordered)
                .buttonBorderShape(.capsule)
        }
    }

    func cardStyle(cornerRadius: CGFloat = HealthBridgeRadius.card) -> some View {
        padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(.secondarySystemGroupedBackground))
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }
}

#Preview {
    ContentView()
}
