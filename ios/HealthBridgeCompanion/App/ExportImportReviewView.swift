import SwiftUI

/// HealthRelay addition: reviews the on-device parse of an Apple Health export.zip
/// before anything is sent to the receiver.
struct ExportImportReviewView: View {
    let summary: AppleHealthExportLabImporter.Summary
    let onConfirm: () -> Void
    let onCancel: () -> Void

    @Environment(\.dismiss) private var dismiss

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
                    Text("Only these extracted values are sent -- the export.zip file itself never leaves this iPhone.")
                }

                if !summary.labResults.isEmpty {
                    Section("Preview") {
                        ForEach(summary.labResults.prefix(5), id: \.clientRecordID) { result in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(result.name)
                                    .font(.subheadline.weight(.semibold))
                                Text(previewDetail(for: result))
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if summary.labResults.count > 5 {
                            Text("and \(summary.labResults.count - 5) more")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
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
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Send") {
                        onConfirm()
                        dismiss()
                    }
                    .disabled(summary.labResults.isEmpty)
                }
            }
        }
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
