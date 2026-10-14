import SwiftUI

/// Sample-data review mode for App Review and screenshots.
///
/// This view reads only `SampleReviewData`. It does not reference the view model, the Apple Health
/// read paths, the receiver client, the mailbox or any network type, so no Health data is read and
/// no receiver is contacted from here. Launch with `-HealthRelaySampleScene <name>` to open a scene
/// directly: overview (default), sync, ecg or labs.
///
/// Colours are local to this file (`SamplePalette`) so the asset catalogue, and every real screen,
/// keeps its existing values. Only the dark scheme, applied at the app root, reaches other screens.
struct SampleReviewView: View {
    private static let banner = "Sample data. Nothing is read from Apple Health or sent anywhere."

    let scene: SampleReviewScene

    @State private var logLines: [String]
    @State private var labImportReviewed = false
    @State private var sampleDisconnected = false

    init(scene: SampleReviewScene = .overview) {
        self.scene = scene
        _logLines = State(initialValue: scene == .sync ? SampleReviewData.syncRunLog : [])
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text(Self.banner)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(SamplePalette.warning)
                        .padding(.horizontal, 18)
                        .padding(.bottom, 10)
                        .accessibilityLabel(Self.banner)

                    HStack(spacing: 8) {
                        Text("HealthRelay")
                            .font(.largeTitle.bold())
                            .foregroundStyle(SamplePalette.textPrimary)
                        Image(systemName: "link")
                            .font(.title3)
                            .foregroundStyle(SamplePalette.accent)
                    }
                    .padding(.horizontal, 18)

                    statusPill
                        .padding(.horizontal, 18)
                        .padding(.top, 12)

                    VStack(spacing: 10) {
                        appleHealthCard
                        compoundCard
                        sourceDetailsCard
                        Text("One drink. One calorie entry.")
                            .font(.footnote)
                            .foregroundStyle(SamplePalette.textTertiary)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 4)
                    }
                    .padding(.horizontal, 12)
                    .padding(.top, 16)

                    Button("Manage connection") {
                        withAnimation { proxy.scrollTo("privacy", anchor: .top) }
                    }
                    .buttonStyle(SamplePrimaryButtonStyle())
                    .padding(.horizontal, 18)
                    .padding(.top, 16)

                    lockFooter
                        .padding(.top, 14)

                    VStack(spacing: 10) {
                        syncCard.id("sync")
                        ecgMedicationCard.id("ecg")
                        labCard.id("labs")
                        privacyCard.id("privacy")
                    }
                    .padding(.horizontal, 12)
                    .padding(.top, 28)
                }
                .padding(.bottom, 24)
            }
            .background(
                LinearGradient(
                    colors: [SamplePalette.backgroundTop, SamplePalette.backgroundBottom],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .ignoresSafeArea()
            )
            .onAppear {
                if scene != .overview {
                    proxy.scrollTo(scene.rawValue, anchor: .top)
                }
            }
        }
        .navigationTitle("Sample Review")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: Overview

    private var statusPill: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(sampleDisconnected ? SamplePalette.tertiaryDot : SamplePalette.accent)
                .frame(width: 8, height: 8)
            Text(sampleDisconnected ? "Disconnected" : "Connected")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(sampleDisconnected ? SamplePalette.textTertiary : SamplePalette.connectedText)
        }
        .padding(.horizontal, 14)
        .frame(height: 37)
        .background(sampleDisconnected ? SamplePalette.neutralFill : SamplePalette.connectedFill, in: Capsule())
        .overlay(
            Capsule().stroke(
                sampleDisconnected ? SamplePalette.neutralBorder : SamplePalette.connectedBorder,
                lineWidth: 1
            )
        )
        .accessibilityElement(children: .combine)
    }

    private var appleHealthCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 14) {
                iconTile(symbol: "heart.fill", symbolColor: SamplePalette.healthHeart, tile: SamplePalette.healthTile)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Apple Health")
                        .font(.headline)
                        .foregroundStyle(SamplePalette.textPrimary)
                    Text("Supported nutrients")
                        .font(.footnote)
                        .foregroundStyle(SamplePalette.textSecondary)
                    Text("Last sync \u{00B7} \(SampleReviewData.lastSyncedLabel)")
                        .font(.footnote)
                        .foregroundStyle(SamplePalette.textTertiary)
                }
                Spacer(minLength: 8)
                chevron
            }
        }
        .sampleCard()
    }

    private var compoundCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 14) {
                iconTile(symbol: "pills", symbolColor: SamplePalette.textPrimary, tile: SamplePalette.compoundTile)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Creatine & other compounds")
                        .font(.headline)
                        .foregroundStyle(SamplePalette.textPrimary)
                    Text("Optional direct connection")
                        .font(.footnote)
                        .foregroundStyle(SamplePalette.textSecondary)
                    pairedBadge
                    Text("Saved locally when offline")
                        .font(.footnote)
                        .foregroundStyle(SamplePalette.textTertiary)
                }
                Spacer(minLength: 8)
                chevron
            }
        }
        .sampleCard()
    }

    private var pairedBadge: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(SamplePalette.badgeDot)
                .frame(width: 6, height: 6)
            Text("Paired receiver")
                .font(.caption.weight(.semibold))
                .foregroundStyle(SamplePalette.badgeText)
        }
        .padding(.horizontal, 10)
        .frame(height: 30)
        .background(SamplePalette.badgeFill, in: Capsule())
        .overlay(Capsule().stroke(SamplePalette.badgeBorder, lineWidth: 1))
        .padding(.vertical, 2)
    }

    private var sourceDetailsCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Source details")
                .font(.headline)
                .foregroundStyle(SamplePalette.textPrimary)
                .padding(.bottom, 6)
            ForEach(Array(SampleSourceKind.allCases.enumerated()), id: \.offset) { index, kind in
                if index > 0 {
                    Rectangle()
                        .fill(SamplePalette.divider)
                        .frame(height: 1)
                        .padding(.leading, 40)
                }
                HStack(spacing: 12) {
                    Image(systemName: kind.symbol)
                        .font(.body)
                        .foregroundStyle(SamplePalette.accent)
                        .frame(width: 28)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(kind.title)
                            .font(.body.weight(.semibold))
                            .foregroundStyle(SamplePalette.textPrimary)
                        Text(kind.subtitle)
                            .font(.footnote)
                            .foregroundStyle(SamplePalette.textSecondary)
                    }
                    Spacer(minLength: 8)
                    Text("\(SampleReviewData.sourceQuantity(kind).formatted()) \(kind.unit)")
                        .font(.subheadline)
                        .foregroundStyle(SamplePalette.textTertiary)
                    chevron
                }
                .padding(.vertical, 12)
            }
        }
        .sampleCard()
    }

    private var lockFooter: some View {
        Label {
            Text("Your local ledger works on its own.")
                .font(.footnote.weight(.medium))
                .foregroundStyle(SamplePalette.footer)
        } icon: {
            Image(systemName: "lock.fill")
                .foregroundStyle(SamplePalette.textPrimary)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: Scene cards (same card, colour and button treatment as the overview)

    private var syncCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Sync run")
            Button("Run sample sync") {
                logLines = SampleReviewData.syncRunLog
            }
            .buttonStyle(SampleSecondaryButtonStyle())
            ForEach(Array(logLines.enumerated()), id: \.offset) { entry in
                Text(entry.element)
                    .font(.caption.monospaced())
                    .foregroundStyle(SamplePalette.textSecondary)
            }
        }
        .sampleCard()
    }

    private var ecgMedicationCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("ECG and medication")
            ForEach(SampleReviewData.records(for: .ecg)) { record in
                valueRow(title: record.title, value: "\(Int(record.quantity)) seconds")
            }
            ForEach(SampleReviewData.records(for: .medication)) { record in
                valueRow(title: record.title, value: "\(record.quantity.formatted()) dose")
            }
        }
        .sampleCard()
    }

    private var labCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Lab import")
            valueRow(title: "Results to review", value: "\(SampleReviewData.pendingLabImportCount)")
            Button(labImportReviewed ? "Sample results reviewed" : "Review sample lab import") {
                labImportReviewed = true
            }
            .buttonStyle(SampleSecondaryButtonStyle())
            .disabled(labImportReviewed)
            Text("Nothing from a lab import is sent until you review it.")
                .font(.footnote)
                .foregroundStyle(SamplePalette.textSecondary)
        }
        .sampleCard()
    }

    private var privacyCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Privacy and disconnect")
            Text("Read-only. Disconnect any time. Sample mode has no real connection to disconnect.")
                .font(.footnote)
                .foregroundStyle(SamplePalette.textSecondary)
            Button("Disconnect sample receiver", role: .destructive) {
                sampleDisconnected = true
            }
            .buttonStyle(SampleSecondaryButtonStyle())
            .disabled(sampleDisconnected)
            if sampleDisconnected {
                Button("Reset sample mode") {
                    sampleDisconnected = false
                    logLines = []
                    labImportReviewed = false
                }
                .buttonStyle(SampleSecondaryButtonStyle())
            }
        }
        .sampleCard()
    }

    // MARK: Shared pieces

    private var chevron: some View {
        Image(systemName: "chevron.right")
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(SamplePalette.chevron)
    }

    private func iconTile(symbol: String, symbolColor: Color, tile: Color) -> some View {
        Image(systemName: symbol)
            .font(.title2)
            .foregroundStyle(symbolColor)
            .frame(width: 56, height: 56)
            .background(tile, in: RoundedRectangle(cornerRadius: 13, style: .continuous))
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(.headline)
            .foregroundStyle(SamplePalette.textPrimary)
    }

    private func valueRow(title: String, value: String) -> some View {
        HStack {
            Text(title)
                .font(.subheadline)
                .foregroundStyle(SamplePalette.textPrimary)
            Spacer(minLength: 8)
            Text(value)
                .font(.subheadline)
                .foregroundStyle(SamplePalette.textTertiary)
        }
    }
}

/// Colours from the mockup, local to the sample screens.
private enum SamplePalette {
    static let backgroundTop = Color(sampleHex: 0x03141A)
    static let backgroundBottom = Color(sampleHex: 0x010809)
    static let cardFill = Color(sampleHex: 0x0D2229)
    static let cardBorder = Color.white.opacity(0.06)
    static let divider = Color(sampleHex: 0x192A31)
    static let accent = Color(sampleHex: 0x23F6C9)
    static let highlight = Color(sampleHex: 0x4AF4E8)
    static let buttonInk = Color(sampleHex: 0x04141A)
    static let connectedFill = Color(sampleHex: 0x032C2E)
    static let connectedBorder = Color(sampleHex: 0x065B55)
    static let connectedText = Color(sampleHex: 0x6FF8EC)
    static let neutralFill = Color.white.opacity(0.06)
    static let neutralBorder = Color.white.opacity(0.12)
    static let tertiaryDot = Color(sampleHex: 0xAFC3D1).opacity(0.7)
    static let badgeFill = Color(sampleHex: 0x052D2E)
    static let badgeBorder = Color(sampleHex: 0x0B5C55)
    static let badgeDot = Color(sampleHex: 0x23F4D1)
    static let badgeText = Color(sampleHex: 0x74F1E2)
    static let textPrimary = Color(sampleHex: 0xFFFFFF)
    static let textSecondary = Color(sampleHex: 0xD0E5ED)
    static let textTertiary = Color(sampleHex: 0xAFC3D1).opacity(0.7)
    static let chevron = Color(sampleHex: 0xB2CCDE)
    static let footer = Color(sampleHex: 0xCFF6F8)
    static let healthTile = Color(sampleHex: 0xFCFCFC)
    static let healthHeart = Color(sampleHex: 0xE0245E)
    static let compoundTile = Color(sampleHex: 0x3A4B52)
    static let warning = Color(sampleHex: 0xF5C451)
}

private extension Color {
    init(sampleHex value: UInt32) {
        self.init(
            .sRGB,
            red: Double((value >> 16) & 0xFF) / 255,
            green: Double((value >> 8) & 0xFF) / 255,
            blue: Double(value & 0xFF) / 255,
            opacity: 1
        )
    }
}

private extension View {
    /// Mockup card: 16pt radius, 17pt inner padding, #0D2229 fill, 1pt white border at 6% alpha.
    func sampleCard() -> some View {
        padding(17)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(SamplePalette.cardFill, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(SamplePalette.cardBorder, lineWidth: 1)
            )
    }
}

/// Primary action: 48pt tall, 16pt corner radius, #4AF4E8 fill with #04141A text.
private struct SamplePrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(SamplePalette.buttonInk)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(SamplePalette.highlight, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.4)
    }
}

/// Secondary action on the sample cards: 48pt tall, 16pt corner radius, outlined in teal.
private struct SampleSecondaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(SamplePalette.connectedText)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(SamplePalette.connectedFill, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(SamplePalette.connectedBorder, lineWidth: 1)
            )
            .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.4)
    }
}

private extension SampleSourceKind {
    var title: String {
        switch self {
        case .energy: "Energy"
        case .water: "Water"
        case .creatine: "Creatine"
        }
    }

    var subtitle: String {
        switch self {
        case .energy: "Food & drinks"
        case .water: "Drink log"
        case .creatine: "Supplement log"
        }
    }

    var symbol: String {
        switch self {
        case .energy: "flame"
        case .water: "drop"
        case .creatine: "pills"
        }
    }

    var unit: String {
        switch self {
        case .energy: "kcal"
        case .water: "L"
        case .creatine: "dose"
        }
    }
}
