import Foundation

public struct HealthBridgeHealthType: Codable, Equatable, Hashable, Sendable {
    public enum Category: String, Codable, Equatable, Hashable, Sendable {
        case activity
        case body
        case heart
        case sleep
        case workout
        case other
    }

    public enum Sensitivity: String, Codable, Equatable, Hashable, Sendable {
        case low
        case moderate
        case high
    }

    public let typeCode: String
    public let displayName: String
    public let category: Category
    public let defaultUnit: String
    public let sensitivity: Sensitivity
    public let aliases: [String]

    public init(
        typeCode: String,
        displayName: String,
        category: Category,
        defaultUnit: String,
        sensitivity: Sensitivity,
        aliases: [String]
    ) {
        self.typeCode = typeCode
        self.displayName = displayName
        self.category = category
        self.defaultUnit = defaultUnit
        self.sensitivity = sensitivity
        self.aliases = aliases
    }

    enum CodingKeys: String, CodingKey {
        case typeCode = "type_code"
        case displayName = "display_name"
        case category
        case defaultUnit = "default_unit"
        case sensitivity
        case aliases
    }

    public static let steps = HealthBridgeHealthType(
        typeCode: "steps",
        displayName: "Steps",
        category: .activity,
        defaultUnit: "count",
        sensitivity: .low,
        aliases: ["HKQuantityTypeIdentifierStepCount"]
    )

    public static let heartRate = HealthBridgeHealthType(
        typeCode: "heart_rate",
        displayName: "Heart Rate",
        category: .heart,
        defaultUnit: "bpm",
        sensitivity: .moderate,
        aliases: ["HKQuantityTypeIdentifierHeartRate"]
    )

    public static let weight = HealthBridgeHealthType(
        typeCode: "weight",
        displayName: "Weight",
        category: .body,
        defaultUnit: "kg",
        sensitivity: .high,
        aliases: ["HKQuantityTypeIdentifierBodyMass", "body_mass"]
    )

    public static let sleepAnalysis = HealthBridgeHealthType(
        typeCode: "sleep_analysis",
        displayName: "Sleep Analysis",
        category: .sleep,
        defaultUnit: "stage",
        sensitivity: .moderate,
        aliases: ["HKCategoryTypeIdentifierSleepAnalysis"]
    )

    public static let workouts = HealthBridgeHealthType(
        typeCode: "workout",
        displayName: "Workout",
        category: .workout,
        defaultUnit: "session",
        sensitivity: .moderate,
        aliases: ["HKWorkoutType"]
    )

    /// HealthRelay addition: a dedicated foreground lane (`syncRecentElectrocardiograms`);
    /// not background-eligible yet.
    public static let electrocardiogram = HealthBridgeHealthType(
        typeCode: "electrocardiogram",
        displayName: "Electrocardiogram",
        category: .heart,
        defaultUnit: "recording",
        sensitivity: .high,
        aliases: ["HKElectrocardiogramType"]
    )

    /// HealthRelay addition. Per-object authorized in HealthKit, so it is never part of the
    /// unified `requestAuthorization` set; the foreground medication lane authorizes it itself.
    public static let medicationDoseEvents = HealthBridgeHealthType(
        typeCode: "medication_dose_event",
        displayName: "Medication Dose Event",
        category: .other,
        defaultUnit: "event",
        sensitivity: .high,
        aliases: ["HKMedicationDoseEventType"]
    )

    /// HealthRelay addition. Not a HealthKit type at all -- Apple Health does not expose
    /// clinical records (FHIR Observations) through HealthKit read authorization, so this
    /// is never part of `dedicatedSyncTypes` or any authorization request; the export
    /// importer sends it after a manual file pick and user confirmation only.
    public static let labResult = HealthBridgeHealthType(
        typeCode: "lab_result",
        displayName: "Lab Result",
        category: .other,
        defaultUnit: "observation",
        sensitivity: .high,
        aliases: []
    )

    public static let canonicalTypes: [HealthBridgeHealthType] = [
        .steps,
        .heartRate,
        .weight,
        .sleepAnalysis,
        .workouts,
        .electrocardiogram,
        .medicationDoseEvents,
        .labResult,
    ]

    public static let dedicatedSyncTypes: [HealthBridgeHealthType] = [
        .steps,
        .workouts,
        .sleepAnalysis,
        .electrocardiogram,
    ]

    public static func resolve(alias: String) -> HealthBridgeHealthType? {
        canonicalTypes.first { $0.aliases.contains(alias) }
    }
}
