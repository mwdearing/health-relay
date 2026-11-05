import Foundation

public struct HealthBridgeBatchEncoder {
    private let encoder: JSONEncoder

    public init() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        self.encoder = encoder
    }

    public func encode(_ batch: HealthBridgeBatchV1) throws -> Data {
        try encoder.encode(batch)
    }

    /// Encoded JSON size of one value with this encoder's settings. A value that cannot be
    /// encoded reports `Int.max` so size checks fail closed instead of passing as empty.
    public func encodedByteCount<Value: Encodable>(of value: Value) -> Int {
        (try? encoder.encode(value))?.count ?? Int.max
    }
}

public enum HealthBridgeUTCFormatter {
    public static func string(from date: Date) -> String {
        let wholeSecondDate = Date(timeIntervalSince1970: floor(date.timeIntervalSince1970))
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return formatter.string(from: wholeSecondDate)
    }

    public static func date(from string: String) -> Date? {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        return formatter.date(from: string)
    }
}

/// Keeps every uploaded batch under the receiver's body limit.
///
/// The receiver answers 413 `batch_too_large` before reading any body larger than
/// `MAX_BATCH_BYTES` (5,000,000 in `receiver/server.py`), and resending the same bytes can
/// never succeed. Lanes that read an unbounded amount of history (raw steps, ECG, lab
/// import) therefore split their output with `bounded(_:maxRecordsPerBatch:)`.
public enum OutboundBatchSizePolicy {
    /// Mirrors the receiver's `MAX_BATCH_BYTES`. Documentation and test reference only.
    public static let receiverMaxBatchBytes = 5_000_000
    /// Budget for the encoded record arrays of one batch. The remainder of the receiver
    /// limit is the safety margin for the envelope (sources, window, cursors).
    public static let defaultMaxRecordBytesPerBatch = 3_000_000

    /// Splits `batch` into batches whose record arrays stay under both caps.
    ///
    /// Only the sample, ECG, lab-result and deletion arrays are split. A batch that also
    /// carries workouts, sleep sessions or medication dose events, or that already fits,
    /// is returned unchanged. The sync cursors ride only on the last batch, so the cursor
    /// checkpoint can apply only after the final batch is accepted. A single ECG recording
    /// that alone exceeds the byte budget is sent summary-only (no voltage series).
    public static func bounded(
        _ batch: HealthBridgeBatchV1,
        maxRecordsPerBatch: Int,
        maxRecordBytesPerBatch: Int = defaultMaxRecordBytesPerBatch
    ) -> [HealthBridgeBatchV1] {
        guard batch.workouts.isEmpty,
              batch.sleepSessions.isEmpty,
              batch.medicationDoseEvents.isEmpty else {
            return [batch]
        }
        let recordLimit = max(1, maxRecordsPerBatch)
        let byteLimit = max(1, maxRecordBytesPerBatch)
        let encoder = HealthBridgeBatchEncoder()
        let recordings = batch.electrocardiograms.map { recording in
            encoder.encodedByteCount(of: recording) > byteLimit
                ? recording.summaryOnly()
                : recording
        }

        var pieces: [Piece] = []
        for chunk in chunks(of: batch.samples, recordLimit: recordLimit, byteLimit: byteLimit, encoder: encoder) {
            pieces.append(Piece(samples: chunk))
        }
        for chunk in chunks(of: recordings, recordLimit: recordLimit, byteLimit: byteLimit, encoder: encoder) {
            pieces.append(Piece(electrocardiograms: chunk))
        }
        for chunk in chunks(of: batch.labResults, recordLimit: recordLimit, byteLimit: byteLimit, encoder: encoder) {
            pieces.append(Piece(labResults: chunk))
        }
        for chunk in chunks(of: batch.deletedRecords, recordLimit: recordLimit, byteLimit: byteLimit, encoder: encoder) {
            pieces.append(Piece(deletedRecords: chunk))
        }
        let shrankARecording = zip(recordings, batch.electrocardiograms).contains { $0 != $1 }
        if pieces.count <= 1 && !shrankARecording {
            return [batch]
        }
        if pieces.isEmpty {
            pieces.append(Piece())
        }

        let lastIndex = pieces.count - 1
        return pieces.enumerated().map { index, piece in
            HealthBridgeBatchV1(
                schemaID: batch.schemaID,
                schemaVersion: batch.schemaVersion,
                generatedAt: batch.generatedAt,
                exportWindow: batch.exportWindow,
                sources: batch.sources,
                healthTypes: batch.healthTypes,
                samples: piece.samples,
                workouts: [],
                electrocardiograms: piece.electrocardiograms,
                medicationDoseEvents: [],
                labResults: piece.labResults,
                sleepSessions: [],
                deletedRecords: piece.deletedRecords,
                sync: HealthBridgeSyncContext(
                    syncWindow: batch.sync.syncWindow,
                    cursors: index == lastIndex ? batch.sync.cursors : []
                )
            )
        }
    }

    private struct Piece {
        var samples: [HealthBridgeSample] = []
        var electrocardiograms: [HealthBridgeElectrocardiogram] = []
        var labResults: [HealthBridgeLabResult] = []
        var deletedRecords: [HealthBridgeDeletedRecord] = []
    }

    private static func chunks<Element: Encodable>(
        of elements: [Element],
        recordLimit: Int,
        byteLimit: Int,
        encoder: HealthBridgeBatchEncoder
    ) -> [[Element]] {
        var result: [[Element]] = []
        var current: [Element] = []
        var currentBytes = 0
        for element in elements {
            // One byte for the array separator.
            let size = min(encoder.encodedByteCount(of: element), Int.max - 1) + 1
            let overflowsBytes = size > byteLimit - currentBytes
            if !current.isEmpty && (current.count >= recordLimit || overflowsBytes) {
                result.append(current)
                current = []
                currentBytes = 0
            }
            current.append(element)
            currentBytes += min(size, byteLimit - currentBytes)
        }
        if !current.isEmpty {
            result.append(current)
        }
        return result
    }
}

extension HealthBridgeElectrocardiogram {
    /// The same recording without its voltage series. The receiver accepts a summary-only
    /// recording and keeps `voltage_count`.
    func summaryOnly() -> HealthBridgeElectrocardiogram {
        HealthBridgeElectrocardiogram(
            clientRecordID: clientRecordID,
            sourceKey: sourceKey,
            startTime: startTime,
            endTime: endTime,
            classification: classification,
            symptomsStatus: symptomsStatus,
            averageHeartRateBPM: averageHeartRateBPM,
            samplingFrequencyHz: samplingFrequencyHz,
            voltageCount: voltageCount,
            voltagesMicrovolts: nil
        )
    }
}
