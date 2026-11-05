import XCTest
@testable import HealthBridgeCompanionCore

final class UploadQuarantineTests: XCTestCase {
    func testClassifierTreatsOnlyPermanentRejectionsAsTerminal() {
        XCTAssertEqual(PermanentUploadRejection.classify(statusCode: 413, errorCode: nil), .payloadTooLarge)
        XCTAssertEqual(PermanentUploadRejection.classify(statusCode: 422, errorCode: nil), .invalidPayload)
        XCTAssertEqual(
            PermanentUploadRejection.classify(statusCode: 400, errorCode: "invalid_json"),
            .invalidPayload
        )
        XCTAssertEqual(
            PermanentUploadRejection.classify(statusCode: 400, errorCode: "invalid_batch"),
            .invalidPayload
        )
        XCTAssertEqual(
            PermanentUploadRejection.classify(statusCode: 403, errorCode: "source_principal_mismatch"),
            .sourcePrincipalMismatch
        )

        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 400, errorCode: nil))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 403, errorCode: nil))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 403, errorCode: "forbidden"))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 401, errorCode: nil))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 409, errorCode: nil))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 429, errorCode: nil))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 500, errorCode: nil))
        XCTAssertNil(PermanentUploadRejection.classify(statusCode: 503, errorCode: nil))
    }

    func testErrorCodeIsReadFromReceiverJSONBody() {
        XCTAssertEqual(
            PermanentUploadRejection.errorCode(
                fromResponseBody: Data(#"{"error":"source_principal_mismatch"}"#.utf8)
            ),
            "source_principal_mismatch"
        )
        XCTAssertNil(PermanentUploadRejection.errorCode(fromResponseBody: Data("not json".utf8)))
        XCTAssertNil(PermanentUploadRejection.errorCode(fromResponseBody: nil))
    }

    func testFinalizerQuarantinesPermanentRejectionOfTheHeadInsteadOfRetainingIt() throws {
        let rejected = try finish(statusCode: 413)

        XCTAssertEqual(rejected.outcome, .quarantined)
        XCTAssertEqual(rejected.quarantined.count, 1)
        XCTAssertEqual(rejected.quarantined.first?.0, "item-1")
        XCTAssertEqual(rejected.quarantined.first?.1, .payloadTooLarge)
        XCTAssertEqual(rejected.quarantined.first?.2, 413)

        let schema = try finish(statusCode: 422)
        XCTAssertEqual(schema.outcome, .quarantined)
        XCTAssertEqual(schema.quarantined.first?.1, .invalidPayload)

        let mismatch = try finish(statusCode: 403, errorCode: "source_principal_mismatch")
        XCTAssertEqual(mismatch.outcome, .quarantined)
        XCTAssertEqual(mismatch.quarantined.first?.1, .sourcePrincipalMismatch)

        // The receiver answers 413 before reading the body, so the transfer may report an error too.
        let withTransportError = try finish(statusCode: 413, hadTransportError: true)
        XCTAssertEqual(withTransportError.outcome, .quarantined)
    }

    func testFinalizerKeepsTransientAndAuthFailuresRetained() throws {
        for statusCode in [401, 409, 429, 500, 502, 503] {
            let result = try finish(statusCode: statusCode)
            XCTAssertEqual(result.outcome, .retained, "HTTP \(statusCode)")
            XCTAssertTrue(result.quarantined.isEmpty, "HTTP \(statusCode)")
        }
        let otherForbidden = try finish(statusCode: 403, errorCode: "forbidden")
        XCTAssertEqual(otherForbidden.outcome, .retained)
        let bareBadRequest = try finish(statusCode: 400)
        XCTAssertEqual(bareBadRequest.outcome, .retained)
        let transportFailure = try finish(statusCode: 422, hadTransportError: true)
        XCTAssertEqual(transportFailure.outcome, .retained)
        XCTAssertTrue(transportFailure.quarantined.isEmpty)
    }

    func testCompletionWithoutResponseErrorCodeStillDecodes() throws {
        let legacy = Data(
            #"{"statusCode":413,"hadTransportError":false}"#.utf8
        )

        let completion = try JSONDecoder().decode(BackgroundUploadTaskCompletion.self, from: legacy)

        XCTAssertEqual(completion.statusCode, 413)
        XCTAssertNil(completion.responseErrorCode)
    }

    func testQuarantiningTheHeadLetsLaterItemsBeUploaded() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = try FileOutbox(directory: directory)
        let rejectedPayload = Data("synthetic-rejected-payload".utf8)
        let head = try outbox.enqueue(rejectedPayload, receiverIdentity: "receiver-a")
        let behind = try outbox.enqueue(Data("synthetic-valid-payload".utf8), receiverIdentity: "receiver-a")
        let now = Date(timeIntervalSince1970: 1_780_000_000)

        let record = try XCTUnwrap(outbox.quarantineItem(
            id: head.id,
            reason: .payloadTooLarge,
            statusCode: 413,
            at: now
        ))

        XCTAssertEqual(record.itemID, head.id)
        XCTAssertEqual(record.byteCount, rejectedPayload.count)
        XCTAssertEqual(try outbox.pendingItems().map(\.id), [behind.id])
        XCTAssertEqual(try outbox.uploadablePendingItems(for: "receiver-a").map(\.id), [behind.id])
        XCTAssertEqual(try outbox.quarantinedItems(), [record])
        // Nothing is dropped silently: the payload is still on disk until the user discards it.
        XCTAssertEqual(
            try Data(contentsOf: directory
                .appendingPathComponent("quarantine")
                .appendingPathComponent(head.id)
                .appendingPathExtension("json")),
            rejectedPayload
        )

        let relaunched = try FileOutbox(directory: directory)
        XCTAssertEqual(try relaunched.pendingItems().map(\.id), [behind.id])
        XCTAssertEqual(try relaunched.quarantinedItems(), [record])
    }

    func testQuarantineDropsTheOwningCursorCheckpointAndKeepsSiblingItems() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = try FileOutbox(directory: directory)
        let checkpoint = FileOutboxCursorCheckpoint(
            receiverIdentity: "receiver-a",
            sourceKey: "apple_health.phone",
            cursorKind: "anchored_step_sync",
            cursorValue: "synthetic-anchor"
        )
        let items = try outbox.enqueueSequence(
            [Data("chunk-1".utf8), Data("chunk-2".utf8)],
            receiverIdentity: "receiver-a",
            cursorCheckpoint: checkpoint
        )
        XCTAssertEqual(try outbox.pendingCursorCheckpoint(), checkpoint)

        _ = try outbox.quarantineItem(id: items[0].id, reason: .invalidPayload, statusCode: 422)

        // The cursor must not advance past data the receiver never accepted.
        XCTAssertNil(try outbox.pendingCursorCheckpoint())
        XCTAssertEqual(try outbox.pendingItems().map(\.id), [items[1].id])
        XCTAssertEqual(try Data(contentsOf: items[1].fileURL), Data("chunk-2".utf8))
        XCTAssertEqual(try outbox.quarantinedItems().map(\.itemID), [items[0].id])
    }

    func testDiscardRemovesQuarantinedPayloadAndRecord() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = try FileOutbox(directory: directory)
        let first = try outbox.enqueue(Data("one".utf8), receiverIdentity: "receiver-a")
        let second = try outbox.enqueue(Data("two".utf8), receiverIdentity: "receiver-a")
        _ = try outbox.quarantineItem(id: first.id, reason: .sourcePrincipalMismatch, statusCode: 403)
        _ = try outbox.quarantineItem(id: second.id, reason: .invalidPayload, statusCode: 422)
        XCTAssertEqual(try outbox.quarantinedItems().count, 2)

        try outbox.discardQuarantinedItem(id: first.id)
        XCTAssertEqual(try outbox.quarantinedItems().map(\.itemID), [second.id])

        XCTAssertEqual(try outbox.discardAllQuarantinedItems(), 1)
        XCTAssertTrue(try outbox.quarantinedItems().isEmpty)
        XCTAssertTrue(try outbox.pendingItems().isEmpty)
    }

    func testQuarantiningAnItemThatIsNoLongerPendingIsANoOp() throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = try FileOutbox(directory: directory)

        XCTAssertNil(try outbox.quarantineItem(id: "no-such-item", reason: .payloadTooLarge, statusCode: 413))
        XCTAssertTrue(try outbox.quarantinedItems().isEmpty)
    }

    private func finish(
        statusCode: Int,
        errorCode: String? = nil,
        hadTransportError: Bool = false
    ) throws -> (outcome: DirectUploadFinalizationOutcome, quarantined: [(String, PermanentUploadRejection, Int)]) {
        var quarantined: [(String, PermanentUploadRejection, Int)] = []
        let outcome = try DirectUploadFinalizer.finish(
            descriptor: DirectUploadCompletionDescriptor(
                itemID: "item-1",
                receiverGeneration: "g1",
                receiverBindingID: "binding-a"
            ),
            completion: BackgroundUploadTaskCompletion(
                statusCode: statusCode,
                hadTransportError: hadTransportError,
                sleepMinimumResetEpoch: nil,
                responseErrorCode: errorCode
            ),
            currentReceiverGeneration: "g1",
            currentReceiverBindingID: "binding-a",
            recoverSleepBaseline: { _, _ in },
            retire: { _, _ in },
            quarantine: { itemID, rejection, status in
                quarantined.append((itemID, rejection, status))
            }
        )
        return (outcome, quarantined)
    }

    private func temporaryDirectory() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("HealthBridgeUploadQuarantineTests")
            .appendingPathComponent(UUID().uuidString)
    }
}
