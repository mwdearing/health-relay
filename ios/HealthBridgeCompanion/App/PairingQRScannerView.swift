import AVFoundation
import SwiftUI
import UIKit
import VisionKit

/// Whether the in-app setup-QR scanner can be offered on this device.
enum SetupQRScanner {
    /// Hidden only when the device has no scanner or the camera is restricted. The scanner's
    /// own `isAvailable` is deliberately not part of this gate: it is false until camera
    /// access has been granted, so using it here would hide the button on a first run (before
    /// the permission prompt) and after a denial (before the guidance). The sheet handles the
    /// not-determined, denied and unavailable states itself.
    @MainActor
    static var isOffered: Bool {
        DataScannerViewController.isSupported
            && AVCaptureDevice.authorizationStatus(for: .video) != .restricted
    }
}

/// Full-screen camera scanner sheet for the setup QR code. It only reports the first scanned
/// string; the caller feeds it into the existing setup-link import.
struct PairingQRScannerSheet: View {
    let onScan: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var access: CameraAccess = .checking

    private enum CameraAccess {
        case checking
        case granted
        case denied
    }

    var body: some View {
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color(.systemGroupedBackground))
                .navigationTitle("Scan Setup QR")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                    }
                }
        }
        .task { access = await Self.resolveAccess() }
        // Coming back from Settings after allowing the camera: the sheet never left the
        // screen, so check again instead of staying on the denied guidance.
        .onChange(of: scenePhase) { _, phase in
            guard phase == .active, access == .denied else { return }
            Task { access = await Self.resolveAccess() }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch access {
        case .checking:
            ProgressView()
        case .denied:
            guidance(
                title: "Camera access is off",
                message: "HealthRelay uses the camera only to scan the setup QR code. To scan it here, allow camera access in Settings. You can also scan the code with the iPhone Camera app, or paste the setup link instead.",
                showsSettingsButton: true
            )
        case .granted:
            if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                PairingQRScannerView { scanned in
                    onScan(scanned)
                    dismiss()
                }
                .ignoresSafeArea(edges: .bottom)
                .overlay(alignment: .bottom) {
                    Text("Point the camera at the QR code on your setup page.")
                        .font(.footnote.weight(.semibold))
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(.regularMaterial, in: Capsule())
                        .padding(.bottom, 24)
                }
            } else {
                guidance(
                    title: "Scanner not available",
                    message: "The camera scanner can't run right now. Scan the code with the iPhone Camera app, or paste the setup link instead.",
                    showsSettingsButton: false
                )
            }
        }
    }

    private func guidance(title: String, message: String, showsSettingsButton: Bool) -> some View {
        VStack(spacing: 14) {
            Image(systemName: "camera.fill")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            Text(title)
                .font(.headline)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            if showsSettingsButton, let url = URL(string: UIApplication.openSettingsURLString) {
                Button("Open Settings") {
                    UIApplication.shared.open(url)
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(24)
    }

    private static func resolveAccess() async -> CameraAccess {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            return .granted
        case .notDetermined:
            return await AVCaptureDevice.requestAccess(for: .video) ? .granted : .denied
        default:
            return .denied
        }
    }
}

/// Wraps VisionKit's data scanner for QR codes only. Delivers at most one string, then stops.
struct PairingQRScannerView: UIViewControllerRepresentable {
    let onScan: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onScan: onScan)
    }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: true,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        guard !context.coordinator.hasDelivered, !scanner.isScanning else { return }
        try? scanner.startScanning()
    }

    static func dismantleUIViewController(_ scanner: DataScannerViewController, coordinator: Coordinator) {
        scanner.stopScanning()
    }

    @MainActor
    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        private let onScan: (String) -> Void
        private(set) var hasDelivered = false

        init(onScan: @escaping (String) -> Void) {
            self.onScan = onScan
        }

        func dataScanner(
            _ dataScanner: DataScannerViewController,
            didAdd addedItems: [RecognizedItem],
            allItems: [RecognizedItem]
        ) {
            deliverFirstPayload(in: addedItems, from: dataScanner)
        }

        func dataScanner(_ dataScanner: DataScannerViewController, didTapOn item: RecognizedItem) {
            deliverFirstPayload(in: [item], from: dataScanner)
        }

        private func deliverFirstPayload(in items: [RecognizedItem], from scanner: DataScannerViewController) {
            guard !hasDelivered else { return }
            for item in items {
                guard case let .barcode(barcode) = item,
                      let payload = barcode.payloadStringValue,
                      !payload.isEmpty else { continue }
                hasDelivered = true
                scanner.stopScanning()
                onScan(payload)
                return
            }
        }
    }
}
