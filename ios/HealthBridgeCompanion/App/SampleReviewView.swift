import SwiftUI

/// Sample-data review mode for App Review.
///
/// This view reads only `SampleReviewData`. It does not reference the view model, the Apple Health
/// read paths, the receiver client, the mailbox or any network type, so no Health data is read and
/// no receiver is contacted from here.
struct SampleReviewView: View {
    private static let banner = "Sample data. Nothing is read from Apple Health or sent anywhere."

    @State private var logLines: [String] = []
    @State private var labImportReviewed = false
    @State private var sampleDisconnected = false

    var body: some View {
        List {
            Section {
                Text(Self.banner)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.orange)
                    .accessibilityLabel(Self.banner)
            }

            Section("Receiver") {
                LabeledContent("Receiver", value: SampleReviewData.receiverName)
                LabeledContent("Address", value: SampleReviewData.receiverHost)
                LabeledContent("Last sync", value: SampleReviewData.lastSyncedISO)
                Text(sampleDisconnected ? "Sample receiver disconnected. Nothing was sent." : "Paired with the sample receiver.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Pairing") {
                Text("Pairing scans a setup code from your own receiver. Sample mode skips pairing and uses the invented receiver above.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Data types") {
                Text("Steps, sleep, workouts, ECG recordings, medication dose events and lab results. You choose which types are read.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Sync run") {
                Button("Run sample sync") {
                    logLines = SampleReviewData.syncRunLog
                }
                ForEach(Array(logLines.enumerated()), id: \.offset) { entry in
                    Text(entry.element)
                        .font(.caption.monospaced())
                }
            }

            Section("ECG and medication") {
                ForEach(SampleReviewData.records(for: .ecg)) { record in
                    LabeledContent(record.title, value: "\(Int(record.quantity)) seconds")
                }
                ForEach(SampleReviewData.records(for: .medication)) { record in
                    LabeledContent(record.title, value: "\(record.quantity, format: .number) dose")
                }
            }

            Section("Lab import") {
                LabeledContent("Results to review", value: "\(SampleReviewData.pendingLabImportCount)")
                Button(labImportReviewed ? "Sample results reviewed" : "Review sample lab import") {
                    labImportReviewed = true
                }
                .disabled(labImportReviewed)
                Text("Nothing from a lab import is sent until you review it.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Section("Privacy and disconnect") {
                Text("Read-only. Disconnect any time. Sample mode has no real connection to disconnect.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button("Disconnect sample receiver", role: .destructive) {
                    sampleDisconnected = true
                }
                .disabled(sampleDisconnected)
                if sampleDisconnected {
                    Button("Reset sample mode") {
                        sampleDisconnected = false
                        logLines = []
                        labImportReviewed = false
                    }
                }
            }
        }
        .navigationTitle("Sample Review")
        .navigationBarTitleDisplayMode(.inline)
    }
}
