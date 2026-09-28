import Foundation

public struct HealthBridgeBatchV1: Codable, Equatable, Sendable {
    public let schemaID: String
    public let schemaVersion: String
    public let generatedAt: String
    public let exportWindow: HealthBridgeTimeWindow
    public let sources: [HealthBridgeSource]
    public let healthTypes: [HealthBridgeHealthType]
    public let samples: [HealthBridgeSample]
    public let workouts: [HealthBridgeWorkout]
    /// HealthRelay addition: optional in the contract. Encoded only when non-empty so
    /// batches without ECG data stay byte-identical to upstream's encoding.
    public let electrocardiograms: [HealthBridgeElectrocardiogram]
    /// HealthRelay addition: optional, encoded only when non-empty.
    public let medicationDoseEvents: [HealthBridgeMedicationDoseEvent]
    public let sleepSessions: [HealthBridgeSleepSession]
    public let deletedRecords: [HealthBridgeDeletedRecord]
    public let sync: HealthBridgeSyncContext

    public init(
        schemaID: String = "health_bridge.batch.v1",
        schemaVersion: String = "1.0.0",
        generatedAt: String,
        exportWindow: HealthBridgeTimeWindow,
        sources: [HealthBridgeSource],
        healthTypes: [HealthBridgeHealthType],
        samples: [HealthBridgeSample],
        workouts: [HealthBridgeWorkout],
        electrocardiograms: [HealthBridgeElectrocardiogram] = [],
        medicationDoseEvents: [HealthBridgeMedicationDoseEvent] = [],
        sleepSessions: [HealthBridgeSleepSession],
        deletedRecords: [HealthBridgeDeletedRecord],
        sync: HealthBridgeSyncContext
    ) {
        self.schemaID = schemaID
        self.schemaVersion = schemaVersion
        self.generatedAt = generatedAt
        self.exportWindow = exportWindow
        self.sources = sources
        self.healthTypes = healthTypes
        self.samples = samples
        self.workouts = workouts
        self.electrocardiograms = electrocardiograms
        self.medicationDoseEvents = medicationDoseEvents
        self.sleepSessions = sleepSessions
        self.deletedRecords = deletedRecords
        self.sync = sync
    }

    enum CodingKeys: String, CodingKey {
        case schemaID = "schema_id"
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case exportWindow = "export_window"
        case sources
        case healthTypes = "health_types"
        case samples
        case workouts
        case electrocardiograms
        case medicationDoseEvents = "medication_dose_events"
        case sleepSessions = "sleep_sessions"
        case deletedRecords = "deleted_records"
        case sync
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaID = try container.decode(String.self, forKey: .schemaID)
        schemaVersion = try container.decode(String.self, forKey: .schemaVersion)
        generatedAt = try container.decode(String.self, forKey: .generatedAt)
        exportWindow = try container.decode(HealthBridgeTimeWindow.self, forKey: .exportWindow)
        sources = try container.decode([HealthBridgeSource].self, forKey: .sources)
        healthTypes = try container.decode([HealthBridgeHealthType].self, forKey: .healthTypes)
        samples = try container.decode([HealthBridgeSample].self, forKey: .samples)
        workouts = try container.decode([HealthBridgeWorkout].self, forKey: .workouts)
        electrocardiograms = try container.decodeIfPresent(
            [HealthBridgeElectrocardiogram].self,
            forKey: .electrocardiograms
        ) ?? []
        medicationDoseEvents = try container.decodeIfPresent(
            [HealthBridgeMedicationDoseEvent].self,
            forKey: .medicationDoseEvents
        ) ?? []
        sleepSessions = try container.decode([HealthBridgeSleepSession].self, forKey: .sleepSessions)
        deletedRecords = try container.decode([HealthBridgeDeletedRecord].self, forKey: .deletedRecords)
        sync = try container.decode(HealthBridgeSyncContext.self, forKey: .sync)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaID, forKey: .schemaID)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(generatedAt, forKey: .generatedAt)
        try container.encode(exportWindow, forKey: .exportWindow)
        try container.encode(sources, forKey: .sources)
        try container.encode(healthTypes, forKey: .healthTypes)
        try container.encode(samples, forKey: .samples)
        try container.encode(workouts, forKey: .workouts)
        if !electrocardiograms.isEmpty {
            try container.encode(electrocardiograms, forKey: .electrocardiograms)
        }
        if !medicationDoseEvents.isEmpty {
            try container.encode(medicationDoseEvents, forKey: .medicationDoseEvents)
        }
        try container.encode(sleepSessions, forKey: .sleepSessions)
        try container.encode(deletedRecords, forKey: .deletedRecords)
        try container.encode(sync, forKey: .sync)
    }
}

public struct HealthBridgeTimeWindow: Codable, Equatable, Sendable {
    public let startTime: String
    public let endTime: String

    public init(startTime: String, endTime: String) {
        self.startTime = startTime
        self.endTime = endTime
    }

    enum CodingKeys: String, CodingKey {
        case startTime = "start_time"
        case endTime = "end_time"
    }
}

public struct HealthBridgeSource: Codable, Equatable, Sendable {
    public enum Kind: String, Codable, Equatable, Sendable {
        case phone
        case watch
        case app
        case manual
    }

    public let sourceKey: String
    public let name: String
    public let kind: Kind
    public let bundleID: String?
    public let deviceModel: String?

    public init(sourceKey: String, name: String, kind: Kind, bundleID: String? = nil, deviceModel: String? = nil) {
        self.sourceKey = sourceKey
        self.name = name
        self.kind = kind
        self.bundleID = bundleID
        self.deviceModel = deviceModel
    }

    enum CodingKeys: String, CodingKey {
        case sourceKey = "source_key"
        case name
        case kind
        case bundleID = "bundle_id"
        case deviceModel = "device_model"
    }
}

public struct HealthBridgeSample: Codable, Equatable, Sendable {
    public let clientRecordID: String
    public let sourceKey: String
    public let typeCode: String
    public let startTime: String
    public let endTime: String
    public let value: Double
    public let unit: String
    public let metadata: [String: String]

    public init(
        clientRecordID: String,
        sourceKey: String,
        typeCode: String,
        startTime: String,
        endTime: String,
        value: Double,
        unit: String,
        metadata: [String: String] = [:]
    ) {
        self.clientRecordID = clientRecordID
        self.sourceKey = sourceKey
        self.typeCode = typeCode
        self.startTime = startTime
        self.endTime = endTime
        self.value = value
        self.unit = unit
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case clientRecordID = "client_record_id"
        case sourceKey = "source_key"
        case typeCode = "type_code"
        case startTime = "start_time"
        case endTime = "end_time"
        case value
        case unit
        case metadata
    }
}

public struct HealthBridgeWorkout: Codable, Equatable, Sendable {
    public let clientRecordID: String
    public let sourceKey: String
    public let workoutType: String
    public let startTime: String
    public let endTime: String
    public let durationSeconds: Int
    public let energyKcal: Double?
    public let distanceMeters: Double?

    public init(
        clientRecordID: String,
        sourceKey: String,
        workoutType: String,
        startTime: String,
        endTime: String,
        durationSeconds: Int,
        energyKcal: Double? = nil,
        distanceMeters: Double? = nil
    ) {
        self.clientRecordID = clientRecordID
        self.sourceKey = sourceKey
        self.workoutType = workoutType
        self.startTime = startTime
        self.endTime = endTime
        self.durationSeconds = durationSeconds
        self.energyKcal = energyKcal
        self.distanceMeters = distanceMeters
    }

    enum CodingKeys: String, CodingKey {
        case clientRecordID = "client_record_id"
        case sourceKey = "source_key"
        case workoutType = "workout_type"
        case startTime = "start_time"
        case endTime = "end_time"
        case durationSeconds = "duration_seconds"
        case energyKcal = "energy_kcal"
        case distanceMeters = "distance_meters"
    }
}

public struct HealthBridgeElectrocardiogram: Codable, Equatable, Sendable {
    public enum Classification: String, Codable, Equatable, Sendable {
        case notSet = "not_set"
        case sinusRhythm = "sinus_rhythm"
        case atrialFibrillation = "atrial_fibrillation"
        case inconclusiveLowHeartRate = "inconclusive_low_heart_rate"
        case inconclusiveHighHeartRate = "inconclusive_high_heart_rate"
        case inconclusivePoorReading = "inconclusive_poor_reading"
        case inconclusiveOther = "inconclusive_other"
        case unrecognized
    }

    public enum SymptomsStatus: String, Codable, Equatable, Sendable {
        case notSet = "not_set"
        case noneReported = "none"
        case present
    }

    public let clientRecordID: String
    public let sourceKey: String
    public let startTime: String
    public let endTime: String
    public let classification: Classification
    public let symptomsStatus: SymptomsStatus
    public let averageHeartRateBPM: Double?
    public let samplingFrequencyHz: Double?
    public let voltageCount: Int
    public let voltagesMicrovolts: [Double]?

    public init(
        clientRecordID: String,
        sourceKey: String,
        startTime: String,
        endTime: String,
        classification: Classification,
        symptomsStatus: SymptomsStatus,
        averageHeartRateBPM: Double? = nil,
        samplingFrequencyHz: Double? = nil,
        voltageCount: Int,
        voltagesMicrovolts: [Double]? = nil
    ) {
        self.clientRecordID = clientRecordID
        self.sourceKey = sourceKey
        self.startTime = startTime
        self.endTime = endTime
        self.classification = classification
        self.symptomsStatus = symptomsStatus
        self.averageHeartRateBPM = averageHeartRateBPM
        self.samplingFrequencyHz = samplingFrequencyHz
        self.voltageCount = voltageCount
        self.voltagesMicrovolts = voltagesMicrovolts
    }

    enum CodingKeys: String, CodingKey {
        case clientRecordID = "client_record_id"
        case sourceKey = "source_key"
        case startTime = "start_time"
        case endTime = "end_time"
        case classification
        case symptomsStatus = "symptoms_status"
        case averageHeartRateBPM = "average_heart_rate_bpm"
        case samplingFrequencyHz = "sampling_frequency_hz"
        case voltageCount = "voltage_count"
        case voltagesMicrovolts = "voltages_microvolts"
    }
}

public struct HealthBridgeMedicationDoseEvent: Codable, Equatable, Sendable {
    public enum Status: String, Codable, Equatable, Sendable {
        case taken
        case skipped
        case notInteracted = "not_interacted"
        case snoozed
        case notLogged = "not_logged"
        case notificationNotSent = "notification_not_sent"
        case unknown
    }

    public let clientRecordID: String
    public let sourceKey: String
    public let medicationName: String
    public let medicationConceptKey: String?
    public let status: Status
    public let statusRaw: Int
    public let startTime: String
    public let scheduledTime: String?
    public let dose: Double?
    public let unit: String?

    public init(
        clientRecordID: String,
        sourceKey: String,
        medicationName: String,
        medicationConceptKey: String? = nil,
        status: Status,
        statusRaw: Int,
        startTime: String,
        scheduledTime: String? = nil,
        dose: Double? = nil,
        unit: String? = nil
    ) {
        self.clientRecordID = clientRecordID
        self.sourceKey = sourceKey
        self.medicationName = medicationName
        self.medicationConceptKey = medicationConceptKey
        self.status = status
        self.statusRaw = statusRaw
        self.startTime = startTime
        self.scheduledTime = scheduledTime
        self.dose = dose
        self.unit = unit
    }

    enum CodingKeys: String, CodingKey {
        case clientRecordID = "client_record_id"
        case sourceKey = "source_key"
        case medicationName = "medication_name"
        case medicationConceptKey = "medication_concept_key"
        case status
        case statusRaw = "status_raw"
        case startTime = "start_time"
        case scheduledTime = "scheduled_time"
        case dose
        case unit
    }
}

public struct HealthBridgeSleepStageInterval: Codable, Equatable, Sendable {
    public let stage: String
    public let startTime: String
    public let endTime: String

    public init(stage: String, startTime: String, endTime: String) {
        self.stage = stage
        self.startTime = startTime
        self.endTime = endTime
    }

    enum CodingKeys: String, CodingKey {
        case stage
        case startTime = "start_time"
        case endTime = "end_time"
    }
}

public struct HealthBridgeSleepSession: Codable, Equatable, Sendable {
    public let clientRecordID: String
    public let sourceKey: String
    public let startTime: String
    public let endTime: String
    public let stageIntervals: [HealthBridgeSleepStageInterval]

    public init(
        clientRecordID: String,
        sourceKey: String,
        startTime: String,
        endTime: String,
        stageIntervals: [HealthBridgeSleepStageInterval]
    ) {
        self.clientRecordID = clientRecordID
        self.sourceKey = sourceKey
        self.startTime = startTime
        self.endTime = endTime
        self.stageIntervals = stageIntervals
    }

    enum CodingKeys: String, CodingKey {
        case clientRecordID = "client_record_id"
        case sourceKey = "source_key"
        case startTime = "start_time"
        case endTime = "end_time"
        case stageIntervals = "stage_intervals"
    }
}

public struct HealthBridgeDeletedRecord: Codable, Equatable, Sendable {
    public let recordFamily: String
    public let sourceKey: String
    public let clientRecordID: String
    public let deletedAt: String

    public init(recordFamily: String, sourceKey: String, clientRecordID: String, deletedAt: String) {
        self.recordFamily = recordFamily
        self.sourceKey = sourceKey
        self.clientRecordID = clientRecordID
        self.deletedAt = deletedAt
    }

    enum CodingKeys: String, CodingKey {
        case recordFamily = "record_family"
        case sourceKey = "source_key"
        case clientRecordID = "client_record_id"
        case deletedAt = "deleted_at"
    }
}

public struct HealthBridgeSyncContext: Codable, Equatable, Sendable {
    public let syncWindow: HealthBridgeTimeWindow
    public let cursors: [HealthBridgeSyncCursor]

    public init(syncWindow: HealthBridgeTimeWindow, cursors: [HealthBridgeSyncCursor]) {
        self.syncWindow = syncWindow
        self.cursors = cursors
    }

    enum CodingKeys: String, CodingKey {
        case syncWindow = "sync_window"
        case cursors
    }
}

public struct HealthBridgeSyncCursor: Codable, Equatable, Sendable {
    public let sourceKey: String
    public let cursorKind: String
    public let cursorValue: String

    public init(sourceKey: String, cursorKind: String, cursorValue: String) {
        self.sourceKey = sourceKey
        self.cursorKind = cursorKind
        self.cursorValue = cursorValue
    }

    enum CodingKeys: String, CodingKey {
        case sourceKey = "source_key"
        case cursorKind = "cursor_kind"
        case cursorValue = "cursor_value"
    }
}
