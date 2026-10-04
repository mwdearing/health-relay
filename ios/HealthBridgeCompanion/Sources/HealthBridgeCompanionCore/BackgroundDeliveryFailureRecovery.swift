import Foundation

public enum BackgroundRecoveryLane: String, Codable, CaseIterable, Sendable {
    case steps, dailyActivity = "daily_activity", workouts, sleep, quantity, medication

    public init(typeCode: String) {
        switch AutomaticSyncDiagnosticLane(typeCode: typeCode) {
        case .steps: self = .steps
        case .dailyActivity: self = .dailyActivity
        case .workouts: self = .workouts
        case .sleep: self = .sleep
        case .medication: self = .medication
        default: self = .quantity
        }
    }
}

/// HealthKit gives a background wake-up roughly 30 seconds before it is reclaimed and applies a
/// backoff to an app that misses the observer completion handler three times; three misses stop
/// background delivery for the app entirely. Acknowledging well inside that window keeps a slow
/// or hung admission cycle from spending the whole budget on one wake-up.
public enum BackgroundObserverAcknowledgementPolicy {
    public static let observerAcknowledgementDeadline: TimeInterval = 15
}

public final class BackgroundObserverAcknowledgement: @unchecked Sendable {
    private let lock = NSLock()
    private var completion: (() -> Void)?

    public init(_ completion: @escaping () -> Void) {
        self.completion = completion
    }

    /// Returns true only for the call that actually delivered the completion handler, so callers can
    /// record which path (admission or deadline) acknowledged HealthKit.
    @discardableResult
    public func call() -> Bool {
        lock.lock()
        let callback = completion
        completion = nil
        lock.unlock()
        callback?()
        return callback != nil
    }
}

public enum BackgroundRecoveryDurableState: String, Codable, Sendable {
    case available, unavailable
}

public struct BackgroundObserverPendingDiagnostic: Codable, Equatable, Sendable {
    public let lanes: Set<BackgroundRecoveryLane>
    public let durableState: BackgroundRecoveryDurableState
}
