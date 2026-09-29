import SwiftUI

/// HealthRelay addition: reviews the on-device parse of an Apple Health export.zip
/// before anything is sent to the receiver. Stays open until the send actually
/// finishes -- previously it dismissed the instant Send was tapped, before the
/// (fire-and-forget) upload had even started, so a failure was invisible (Michael,
/// 2026-09-28: "hit send, it went back to the main app screen", nothing landed
/// server-side; traced to this plus the [Export] activity-log sanitizer gap).
struct ExportImportReviewView: View {
    @ObservedObject var viewModel: HealthBridgeCompanionViewModel
    let summary: AppleHealthExportLabImporter.Summary
    let onCancel: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var isSending = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("Lab results found", value: "\(summary.labResults.count)")
                    if summary.skippedCount > 0 {
                        LabeledContent("Skipped (no usable value or date)", value: "\(summary.skippedCount)")
                    }
                } header: {
                    Text("Review")
                } footer: {
                    Text("Only these extracted values are sent. The export.zip file itself never leaves this iPhone.")
                }

                if !summary.labResults.isEmpty {
                    Section("Preview") {
                        ForEach(summary.labResults.prefix(5), id: \.clientRecordID) { result in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(result.name)
                                    .font(.subheadline.weight(.semibold))
                                Text(previewDetail(for: result))
                                    .font(.footnote)
                                    .foregroundStyle(Color("RelaySecondaryText"))
                            }
                        }
                        if summary.labResults.count > 5 {
                            Text("and \(summary.labResults.count - 5) more")
                                .font(.footnote)
                                .foregroundStyle(Color("RelaySecondaryText"))
                        }
                    }
                }

                if isSending || viewModel.statusMessage.hasPrefix("[Export]") {
                    Section {
                        Label(
                            CompanionPrimaryStatusMessage.sanitized(
                                from: viewModel.statusMessage,
                                isError: viewModel.statusIsError
                            ),
                            systemImage: viewModel.statusIsError ? "exclamationmark.triangle.fill" : "arrow.up.circle"
                        )
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(viewModel.statusIsError ? Color("RelayFailedInk") : Color("RelaySecondaryText"))
                    }
                }
            }
            .navigationTitle("Import Health Export")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        onCancel()
                        dismiss()
                    }
                    .disabled(isSending)
                }
                ToolbarItem(placement: .confirmationAction) {
                    if isSending {
                        ProgressView()
                    } else {
                        Button("Send") {
                            isSending = true
                            Task {
                                await viewModel.confirmPendingAppleHealthExportImport()
                                isSending = false
                                // Success clears pendingExportImportSummary, which the
                                // presenting view's binding turns into a dismiss; on
                                // failure it stays non-nil so this sheet stays open with
                                // the error shown above, ready to retry or cancel.
                            }
                        }
                        .disabled(summary.labResults.isEmpty)
                    }
                }
            }
        }
        // Swiping the sheet away mid-send would cancel the pending import; Cancel is the way out.
        .interactiveDismissDisabled(isSending)
        // Sheets do not reliably inherit the presenting view's tint.
        .tint(Color("AccentColor"))
    }

    private func previewDetail(for result: HealthBridgeLabResult) -> String {
        var parts = [result.effectiveDate]
        if let valueNum = result.valueNum {
            let unit = result.unit.map { " \($0)" } ?? ""
            parts.append("\(valueNum)\(unit)")
        } else if let valueText = result.valueText {
            parts.append(valueText)
        }
        return parts.joined(separator: " \u{00B7} ")
    }
}
