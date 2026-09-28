import Foundation

#if canImport(HealthKit)
import HealthKit
#endif

/// One Apple Watch ECG recording as read from HealthKit (HealthRelay addition).
public struct HealthKitElectrocardiogramSummary: Equatable, Sendable {
    public let uuid: UUID
    public let start: Date
    public let end: Date
    public let classification: HealthBridgeElectrocardiogram.Classification
    public let symptomsStatus: HealthBridgeElectrocardiogram.SymptomsStatus
    public let averageHeartRateBPM: Double?
    public let samplingFrequencyHz: Double?
    public let voltageCount: Int
    public let voltagesMicrovolts: [Double]?

    public init(
        uuid: UUID,
        start: Date,
        end: Date,
        classification: HealthBridgeElectrocardiogram.Classification,
        symptomsStatus: HealthBridgeElectrocardiogram.SymptomsStatus,
        averageHeartRateBPM: Double?,
        samplingFrequencyHz: Double?,
        voltageCount: Int,
        voltagesMicrovolts: [Double]?
    ) {
        self.uuid = uuid
        self.start = start
        self.end = end
        self.classification = classification
        self.symptomsStatus = symptomsStatus
        self.averageHeartRateBPM = averageHeartRateBPM
        self.samplingFrequencyHz = samplingFrequencyHz
        self.voltageCount = voltageCount
        self.voltagesMicrovolts = voltagesMicrovolts
    }
}

public enum HealthKitElectrocardiogramReaderError: Error, Equatable {
    case healthDataUnavailable
    case invalidWindow
}

#if canImport(HealthKit)
public final class HealthKitElectrocardiogramReader: @unchecked Sendable {
    private let healthStore: HKHealthStore

    public init(healthStore: HKHealthStore = HKHealthStore()) {
        self.healthStore = healthStore
    }

    /// Reads ECG samples in `[start, end)` sorted by start date. With `includeVoltages`
    /// each sample's voltage series is fetched with a second query (about 15k values per
    /// 30-second recording); summary-only reads leave `voltagesMicrovolts` nil.
    public func readElectrocardiograms(
        start: Date,
        end: Date,
        includeVoltages: Bool = true
    ) async throws -> [HealthKitElectrocardiogramSummary] {
        guard HKHealthStore.isHealthDataAvailable() else {
            throw HealthKitElectrocardiogramReaderError.healthDataUnavailable
        }
        guard start < end else {
            throw HealthKitElectrocardiogramReaderError.invalidWindow
        }

        let ecgType = HKObjectType.electrocardiogramType()
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end, options: [.strictStartDate])
        let sortDescriptor = NSSortDescriptor(key: HKSampleSortIdentifierStartDate, ascending: true)

        let samples: [HKElectrocardiogram] = try await executeCancellableHealthKitQuery(healthStore: healthStore) { completion in
            HKSampleQuery(
                sampleType: ecgType,
                predicate: predicate,
                limit: HKObjectQueryNoLimit,
                sortDescriptors: [sortDescriptor]
            ) { _, samples, error in
                if let error {
                    completion(.failure(error))
                    return
                }
                completion(.success((samples as? [HKElectrocardiogram]) ?? []))
            }
        }

        var summaries: [HealthKitElectrocardiogramSummary] = []
        summaries.reserveCapacity(samples.count)
        for sample in samples {
            let voltages = includeVoltages ? try await readVoltages(for: sample) : nil
            summaries.append(summary(for: sample, voltages: voltages))
        }
        return summaries
    }

    private func readVoltages(for sample: HKElectrocardiogram) async throws -> [Double] {
        let accumulator = VoltageAccumulator(expectedCount: sample.numberOfVoltageMeasurements)
        return try await executeCancellableHealthKitQuery(healthStore: healthStore) { completion in
            HKElectrocardiogramQuery(sample) { _, result in
                switch result {
                case .measurement(let measurement):
                    if let quantity = measurement.quantity(for: .appleWatchSimilarToLeadI) {
                        accumulator.append(quantity.doubleValue(for: .voltUnit(with: .micro)))
                    } else {
                        accumulator.append(.nan)
                    }
                case .done:
                    completion(.success(accumulator.values))
                case .error(let error):
                    completion(.failure(error))
                @unknown default:
                    completion(.success(accumulator.values))
                }
            }
        }
    }

    private func summary(for sample: HKElectrocardiogram, voltages: [Double]?) -> HealthKitElectrocardiogramSummary {
        HealthKitElectrocardiogramSummary(
            uuid: sample.uuid,
            start: sample.startDate,
            end: sample.endDate,
            classification: classification(for: sample.classification),
            symptomsStatus: symptomsStatus(for: sample.symptomsStatus),
            averageHeartRateBPM: sample.averageHeartRate?.doubleValue(for: .count().unitDivided(by: .minute())),
            samplingFrequencyHz: sample.samplingFrequency?.doubleValue(for: .hertz()),
            voltageCount: sample.numberOfVoltageMeasurements,
            voltagesMicrovolts: voltages
        )
    }

    private func classification(
        for value: HKElectrocardiogram.Classification
    ) -> HealthBridgeElectrocardiogram.Classification {
        switch value {
        case .notSet: return .notSet
        case .sinusRhythm: return .sinusRhythm
        case .atrialFibrillation: return .atrialFibrillation
        case .inconclusiveLowHeartRate: return .inconclusiveLowHeartRate
        case .inconclusiveHighHeartRate: return .inconclusiveHighHeartRate
        case .inconclusivePoorReading: return .inconclusivePoorReading
        case .inconclusiveOther: return .inconclusiveOther
        case .unrecognized: return .unrecognized
        @unknown default: return .unrecognized
        }
    }

    private func symptomsStatus(
        for value: HKElectrocardiogram.SymptomsStatus
    ) -> HealthBridgeElectrocardiogram.SymptomsStatus {
        switch value {
        case .notSet: return .notSet
        case .none: return .noneReported
        case .present: return .present
        @unknown default: return .notSet
        }
    }
}

private final class VoltageAccumulator: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [Double]

    init(expectedCount: Int) {
        storage = []
        storage.reserveCapacity(max(0, expectedCount))
    }

    func append(_ value: Double) {
        lock.lock()
        storage.append(value)
        lock.unlock()
    }

    var values: [Double] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}
#endif
