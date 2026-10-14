import SwiftUI

/// First-run disclosure. It covers the whole app until the user accepts the current copy, and
/// the send path checks the same stored record before any request leaves the phone.
struct SyncConsentView: View {
    let onAccept: () -> Void
    let onNotNow: () -> Void

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Before HealthRelay sends anything")
                        .font(.title2.weight(.semibold))
                        .accessibilityAddTraits(.isHeader)
                    Text("Please read how your data is used. You can review or withdraw this consent in Settings at any time.")
                        .font(.body)
                        .foregroundStyle(.secondary)
                    SyncConsentDisclosure()
                    Button(action: onAccept) {
                        Text("I Agree")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityHint("Allows HealthRelay to send the data you allow to your receiver")
                    Button(action: onNotNow) {
                        Text("Not Now")
                            .font(.headline)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.bordered)
                    Text("Until you agree, nothing is sent. You can agree later in Settings > Data Sharing.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 20)
            }
            .navigationTitle("Data Sharing")
            .navigationBarTitleDisplayMode(.inline)
        }
        .interactiveDismissDisabled()
    }
}

/// The disclosure copy, shared by the first-run screen and the Settings review.
struct SyncConsentDisclosure: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            disclosureItem(
                title: "What is read",
                text: "HealthRelay reads only the Apple Health data types you allow in Apple's permission sheet. It does not interpret that data."
            )
            disclosureItem(
                title: "Where it goes",
                text: "Data is sent only to the receiver you have paired with this iPhone, and only through the connection you set up."
            )
            disclosureItem(
                title: "AI agents",
                text: "Your receiver may let an AI agent that you choose read the data it holds. HealthRelay does not pick that agent, and the choice is yours to make on your receiver."
            )
            disclosureItem(
                title: "Disconnect and revoke",
                text: "You can disconnect in HealthRelay Settings, which stops sending. To revoke read access, open the Health app > profile picture > Privacy > Apps > HealthRelay."
            )
        }
    }

    private func disclosureItem(title: String, text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.headline)
            Text(text)
                .font(.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Settings row destination: shows the accepted state, the disclosure, and withdrawal.
struct SyncConsentReviewView: View {
    @ObservedObject var viewModel: HealthBridgeCompanionViewModel
    @State private var showWithdrawConfirmation = false

    var body: some View {
        List {
            Section("Status") {
                if let record = viewModel.syncConsentRecord {
                    LabeledContent(
                        "Accepted",
                        value: record.acceptedAt.formatted(date: .abbreviated, time: .shortened)
                    )
                    Button("Withdraw Consent", role: .destructive) {
                        showWithdrawConfirmation = true
                    }
                } else {
                    Text("Consent is withdrawn. HealthRelay will not send data until you accept again.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Button("Accept Again") {
                        viewModel.acceptSyncConsent()
                    }
                }
            }

            Section("What you agreed to") {
                SyncConsentDisclosure()
                    .padding(.vertical, 4)
            }
        }
        .navigationTitle("Data Sharing")
        .confirmationDialog(
            "Withdraw consent?",
            isPresented: $showWithdrawConfirmation,
            titleVisibility: .visible
        ) {
            Button("Withdraw Consent", role: .destructive) {
                Task { await viewModel.withdrawSyncConsent() }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Nothing is sent until you accept again. Queued uploads stay on this iPhone.")
        }
    }
}
