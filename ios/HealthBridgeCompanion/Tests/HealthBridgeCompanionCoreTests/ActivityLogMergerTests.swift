import XCTest
@testable import HealthBridgeCompanionCore

final class ActivityLogMergerTests: XCTestCase {
    private func run(_ steps: [(label: String, lane: String?)]) -> [String] {
        var entries: [String] = []
        for (index, step) in steps.enumerated() {
            entries = ActivityLogMerger.appending(
                label: step.label,
                lane: step.lane,
                time: String(format: "08:%02d", index),
                to: entries
            )
        }
        return entries
    }

    func testLaneNameFromManualMessage() {
        XCTAssertEqual(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] lane steps"), ActivityLogLaneUpdate.started("Steps"))
        XCTAssertEqual(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] lane dailyActivity"), ActivityLogLaneUpdate.started("Daily activity"))
        XCTAssertEqual(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] lane electrocardiograms"), ActivityLogLaneUpdate.started("ECG"))
        XCTAssertEqual(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] lane medicationDoseEvents"), ActivityLogLaneUpdate.started("Medications"))
        XCTAssertEqual(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] lane supportedQuantities"), ActivityLogLaneUpdate.started("Other metrics"))
        XCTAssertEqual(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] all lanes finished"), ActivityLogLaneUpdate.finished)
        XCTAssertNil(ActivityLogMerger.laneUpdate(fromManualMessage: "[Manual] lanes starting: 7"))
        XCTAssertNil(ActivityLogMerger.laneUpdate(fromManualMessage: "Sync in progress"))
    }

    func testRowsCarryTheLaneName() {
        let entries = run([("Sync in progress", "Steps"), ("Sync progress saved", "Steps")])
        XCTAssertEqual(entries, ["08:00 — Steps: Sync in progress", "08:01 — Steps: Sync progress saved"])
    }

    func testRowsWithoutALaneStayUnprefixed() {
        XCTAssertEqual(run([("Sync started", nil)]), ["08:00 — Sync started"])
    }

    func testAlternatingProgressAndEmptyResultsCollapseIntoOneCountedRow() {
        var steps: [(label: String, lane: String?)] = []
        for _ in 0..<5 {
            steps.append(("Sync in progress", "Other metrics"))
            steps.append(("No new data to send", "Other metrics"))
        }
        let entries = run(steps)
        XCTAssertEqual(entries, ["08:09 — Other metrics: No new data to send (×5)"])
    }

    func testEmptyResultDoesNotSwallowAProgressRowFromAnotherLane() {
        let entries = run([
            ("Sync in progress", "Steps"),
            ("No new data to send", "Sleep"),
        ])
        XCTAssertEqual(entries, ["08:00 — Steps: Sync in progress", "08:01 — Sleep: No new data to send"])
    }

    func testEmptyResultsInDifferentLanesStaySeparate() {
        let entries = run([
            ("No new data to send", "Steps"),
            ("No new data to send", "Workouts"),
            ("No new data to send", "Workouts"),
        ])
        XCTAssertEqual(entries, ["08:00 — Steps: No new data to send", "08:02 — Workouts: No new data to send (×2)"])
    }

    func testARealResultBreaksTheEmptyRun() {
        let entries = run([
            ("No new data to send", "Other metrics"),
            ("Sync progress saved", "Other metrics"),
            ("No new data to send", "Other metrics"),
        ])
        XCTAssertEqual(entries.count, 3)
        XCTAssertEqual(entries.last, "08:02 — Other metrics: No new data to send")
    }

    func testConsecutiveIdenticalRowsAreStillDeduped() {
        let entries = run([("Sync complete", nil), ("Sync complete", nil)])
        XCTAssertEqual(entries, ["08:00 — Sync complete"])
    }

    func testLogKeepsOnlyTheLatestThirtyRows() {
        var entries: [String] = []
        for index in 0..<40 {
            entries = ActivityLogMerger.appending(label: "Step \(index)", lane: nil, time: "08:00", to: entries)
        }
        XCTAssertEqual(entries.count, 30)
        XCTAssertEqual(entries.first, "08:00 — Step 10")
    }
}

final class ActivityLogRowIdentityTests: XCTestCase {
    func testIdenticalEntriesGetDistinctIDs() {
        let rows = ActivityLogMerger.identifiedRows(from: ["a", "b", "a"])

        XCTAssertEqual(Set(rows.map(\.id)).count, 3)
        XCTAssertEqual(rows.map(\.text), ["a", "b", "a"])
    }

    func testTrimmingOldestEntryKeepsRemainingRowIDs() {
        let before = ActivityLogMerger.identifiedRows(from: ["10:00 — x", "10:01 — y", "10:02 — z"])
        let after = ActivityLogMerger.identifiedRows(from: ["10:01 — y", "10:02 — z", "10:03 — w"])

        XCTAssertEqual(before[1].id, after[0].id)
        XCTAssertEqual(before[2].id, after[1].id)
    }

    func testEmptyListYieldsNoRows() {
        XCTAssertTrue(ActivityLogMerger.identifiedRows(from: []).isEmpty)
    }
}

final class ActivityLogExportTextTests: XCTestCase {
    func testExportIsTheDisplayedRowsOldestFirstOnePerLine() {
        let entries = ["08:00 — Steps: Sync in progress", "08:01 — Steps: Sync progress saved"]
        XCTAssertEqual(
            ActivityLogMerger.exportText(from: entries),
            "08:00 — Steps: Sync in progress\n08:01 — Steps: Sync progress saved"
        )
    }

    func testEmptyLogExportsEmptyText() {
        XCTAssertEqual(ActivityLogMerger.exportText(from: []), "")
    }

    func testRowsCarryingPairingMaterialAreNeverExported() {
        let entries = [
            "08:00 — Sync started",
            "08:01 — opened healthrelay://pair?payload=AAAA",
            "08:02 — key hbi_synthetic_secret leaked",
            "08:03 — Sync finished",
        ]
        XCTAssertEqual(
            ActivityLogMerger.exportText(from: entries),
            "08:00 — Sync started\n08:03 — Sync finished"
        )
    }
}
