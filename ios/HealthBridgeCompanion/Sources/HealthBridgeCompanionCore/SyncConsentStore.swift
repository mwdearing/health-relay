import Foundation

/// An accepted first-run disclosure: which copy the user accepted and when.
public struct SyncConsentRecord: Equatable, Sendable {
    public let version: Int
    public let acceptedAt: Date

    public init(version: Int, acceptedAt: Date) {
        self.version = version
        self.acceptedAt = acceptedAt
    }
}

/// The one answer to "may the app send health data?". Every send path asks this before any
/// outbound request, including automatic sync enqueueing and background uploads.
public enum SyncConsentPolicy {
    /// Bump when the disclosure copy changes so every install accepts the new wording again.
    public static let currentVersion = 1

    public static func maySend(record: SyncConsentRecord?, currentVersion: Int = currentVersion) -> Bool {
        guard let record else { return false }
        return record.version == currentVersion
    }
}

/// Thrown by egress primitives when the persisted record does not admit sending.
public struct SyncConsentRefusal: LocalizedError {
    public init() {}

    public var errorDescription: String? {
        "Accept the data-sharing disclosure in Settings before sending."
    }
}

/// Authoritative check for egress primitives. It reads the persisted store at call time and fails
/// closed, so no caller can send from a stale in-memory copy of the consent state.
public func requirePersistedSyncConsent(_ store: SyncConsentStore = SyncConsentStore()) throws {
    guard store.maySend else { throw SyncConsentRefusal() }
}

/// Local storage for the consent record. Withdrawing removes the record, which closes the gate.
public struct SyncConsentStore {
    private static let versionKey = "healthrelay.syncConsent.version"
    private static let acceptedAtKey = "healthrelay.syncConsent.acceptedAt"

    private let userDefaults: UserDefaults

    public init(userDefaults: UserDefaults = .standard) {
        self.userDefaults = userDefaults
    }

    public var record: SyncConsentRecord? {
        guard userDefaults.object(forKey: Self.versionKey) != nil,
              userDefaults.object(forKey: Self.acceptedAtKey) != nil else {
            return nil
        }
        return SyncConsentRecord(
            version: userDefaults.integer(forKey: Self.versionKey),
            acceptedAt: Date(timeIntervalSince1970: userDefaults.double(forKey: Self.acceptedAtKey))
        )
    }

    public var maySend: Bool {
        SyncConsentPolicy.maySend(record: record)
    }

    public func accept(now: Date = Date()) {
        userDefaults.set(SyncConsentPolicy.currentVersion, forKey: Self.versionKey)
        userDefaults.set(now.timeIntervalSince1970, forKey: Self.acceptedAtKey)
    }

    public func withdraw() {
        userDefaults.removeObject(forKey: Self.versionKey)
        userDefaults.removeObject(forKey: Self.acceptedAtKey)
    }
}
