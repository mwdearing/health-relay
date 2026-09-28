// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "HealthBridgeCompanion",
    platforms: [.iOS(.v18), .macOS(.v13)],
    products: [
        .library(name: "HealthBridgeCompanionCore", targets: ["HealthBridgeCompanionCore"]),
    ],
    dependencies: [
        // HealthRelay addition: reads export.zip on-device for the lab-results importer
        // (Apple's export.zip uses standard deflate; Foundation has no built-in unzip).
        .package(url: "https://github.com/weichsel/ZIPFoundation.git", from: "0.9.19"),
    ],
    targets: [
        .target(
            name: "HealthBridgeCompanionCore",
            dependencies: [
                .product(name: "ZIPFoundation", package: "ZIPFoundation"),
            ]
        ),
        .testTarget(
            name: "HealthBridgeCompanionCoreTests",
            dependencies: ["HealthBridgeCompanionCore"]
        ),
    ]
)
