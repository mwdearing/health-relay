import XCTest
@testable import HealthBridgeCompanionCore

/// Tests for `MinimalZipReader`, including ZIP64 archives.
///
/// All fixtures are tiny synthetic archives (two text/JSON entries, no real data),
/// generated with Python's stdlib (`zipfile`, `zlib`, `struct`) so they cannot share a
/// bug with the Swift reader. Python's `zipfile` only writes ZIP64 structures when a
/// size or offset really exceeds 4 GiB, so the central-directory and end-of-central-
/// directory ZIP64 paths are built by hand with `struct`, then checked to open cleanly
/// with `zipfile` itself:
/// - `normal`: classic archive (deflate + stored entry).
/// - `pythonZip64`: written by `zipfile` with `force_zip64=True` (ZIP64 local headers).
/// - `zip64Sentinel`: central directory entries carry 0xFFFFFFFF sentinels plus the
///   0x0001 extra field (uncompressed size, compressed size, local header offset).
/// - `zip64Full`: as above plus a ZIP64 end-of-central-directory record and locator,
///   with 0xFFFF / 0xFFFFFFFF sentinels in the classic EOCD.
/// - `cappedCount`: `normal` layout with the EOCD entry count set to 0xFFFF and no
///   ZIP64 records (sizes and offsets are real, so it must still open).
/// - `cappedCountMultiDisk`: `cappedCount` with non-zero disk fields (must be rejected).
/// - `encrypted`, `unknownMethod`, `multiDisk`: the `normal` layout with the
///   encryption flag set, compression method 12 (bzip2), or a nonzero disk number.
final class MinimalZipReaderTests: XCTestCase {
    private static let normalBase64 = "UEsDBBQAAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBBQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECFAAUAAAACAAAACEAuh2rfjgAAADUAAAAEwAAAAAAAAAAAAAAAAAAAAAAZXhwb3J0L25vdGVzL2EuanNvblBLAQIUABQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAAAAAAAAAAAAAAGkAAABleHBvcnQvbm90ZXMvYi50eHRQSwUGAAAAAAIAAgCBAAAAsAAAAAAA"
    private static let pythonZip64Base64 = "UEsDBC0AAAAIAAAAIVy6Hat+//////////8TABQAZXhwb3J0L25vdGVzL2EuanNvbgEAEADUAAAAAAAAADgAAAAAAAAAq1YqSi3OLy1KTg2pLEhVslJQ8k8qTi0qSyzJzM9T0lFQykwBCRZX5pVkpJZkJusaKtVyVQ9uTQBQSwMELQAAAAAAAAAhXHTJEDX//////////xIAFABleHBvcnQvbm90ZXMvYi50eHQBABAAFwAAAAAAAAAXAAAAAAAAAHN5bnRoZXRpYyBzdG9yZWQgZW50cnkKUEsBAi0DLQAAAAgAAAAhXLodq344AAAA1AAAABMAAAAAAAAAAAAAAIABAAAAAGV4cG9ydC9ub3Rlcy9hLmpzb25QSwECLQMtAAAAAAAAACFcdMkQNRcAAAAXAAAAEgAAAAAAAAAAAAAAgAF9AAAAZXhwb3J0L25vdGVzL2IudHh0UEsFBgAAAAACAAIAgQAAANgAAAAAAA=="
    private static let zip64SentinelBase64 = "UEsDBC0AAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBC0AAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECLQAtAAAACAAAACEAuh2rfv//////////EwAcAAAAAAAAAAAAAAD/////ZXhwb3J0L25vdGVzL2EuanNvbgEAGADUAAAAAAAAADgAAAAAAAAAAAAAAAAAAABQSwECLQAtAAAAAAAAACEAdMkQNf//////////EgAcAAAAAAAAAAAAAAD/////ZXhwb3J0L25vdGVzL2IudHh0AQAYABcAAAAAAAAAFwAAAAAAAABpAAAAAAAAAFBLBQYAAAAAAgACALkAAACwAAAAAAA="
    private static let zip64FullBase64 = "UEsDBC0AAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBC0AAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECLQAtAAAACAAAACEAuh2rfv//////////EwAcAAAAAAAAAAAAAAD/////ZXhwb3J0L25vdGVzL2EuanNvbgEAGADUAAAAAAAAADgAAAAAAAAAAAAAAAAAAABQSwECLQAtAAAAAAAAACEAdMkQNf//////////EgAcAAAAAAAAAAAAAAD/////ZXhwb3J0L25vdGVzL2IudHh0AQAYABcAAAAAAAAAFwAAAAAAAABpAAAAAAAAAFBLBgYsAAAAAAAAAC0ALQAAAAAAAAAAAAIAAAAAAAAAAgAAAAAAAAC5AAAAAAAAALAAAAAAAAAAUEsGBwAAAABpAQAAAAAAAAEAAABQSwUG/////////////////////wAA"
    private static let encryptedBase64 = "UEsDBBQAAQAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBBQAAQAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECFAAUAAEACAAAACEAuh2rfjgAAADUAAAAEwAAAAAAAAAAAAAAAAAAAAAAZXhwb3J0L25vdGVzL2EuanNvblBLAQIUABQAAQAAAAAAIQB0yRA1FwAAABcAAAASAAAAAAAAAAAAAAAAAGkAAABleHBvcnQvbm90ZXMvYi50eHRQSwUGAAAAAAIAAgCBAAAAsAAAAAAA"
    private static let unknownMethodBase64 = "UEsDBBQAAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBBQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECFAAUAAAADAAAACEAuh2rfjgAAADUAAAAEwAAAAAAAAAAAAAAAAAAAAAAZXhwb3J0L25vdGVzL2EuanNvblBLAQIUABQAAAAMAAAAIQB0yRA1FwAAABcAAAASAAAAAAAAAAAAAAAAAGkAAABleHBvcnQvbm90ZXMvYi50eHRQSwUGAAAAAAIAAgCBAAAAsAAAAAAA"
    private static let cappedCountBase64 = "UEsDBBQAAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBBQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECFAAUAAAACAAAACEAuh2rfjgAAADUAAAAEwAAAAAAAAAAAAAAAAAAAAAAZXhwb3J0L25vdGVzL2EuanNvblBLAQIUABQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAAAAAAAAAAAAAAGkAAABleHBvcnQvbm90ZXMvYi50eHRQSwUGAAAAAP////+BAAAAsAAAAAAA"
    private static let cappedCountMultiDiskBase64 = "UEsDBBQAAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBBQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECFAAUAAAACAAAACEAuh2rfjgAAADUAAAAEwAAAAAAAAAAAAAAAAAAAAAAZXhwb3J0L25vdGVzL2EuanNvblBLAQIUABQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAAAAAAAAAAAAAAGkAAABleHBvcnQvbm90ZXMvYi50eHRQSwUGAQABAP////+BAAAAsAAAAAAA"
    private static let multiDiskBase64 = "UEsDBBQAAAAIAAAAIQC6Hat+OAAAANQAAAATAAAAZXhwb3J0L25vdGVzL2EuanNvbqtWKkotzi8tSk4NqSxIVbJSUPJPKk4tKkssyczPU9JRUMpMAQkWV+aVZKSWZCbrGirVclUPbk0AUEsDBBQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAZXhwb3J0L25vdGVzL2IudHh0c3ludGhldGljIHN0b3JlZCBlbnRyeQpQSwECFAAUAAAACAAAACEAuh2rfjgAAADUAAAAEwAAAAAAAAAAAAAAAAAAAAAAZXhwb3J0L25vdGVzL2EuanNvblBLAQIUABQAAAAAAAAAIQB0yRA1FwAAABcAAAASAAAAAAAAAAAAAAAAAGkAAABleHBvcnQvbm90ZXMvYi50eHRQSwUGAQABAAIAAgCBAAAAsAAAAAAA"

    private static let jsonEntry = String(
        repeating: "{\"resourceType\": \"Observation\", \"id\": \"synthetic-1\"}\n",
        count: 4
    )
    private static let textEntry = "synthetic stored entry\n"

    private func writeZip(_ base64: String) throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("fixture.zip")
        try XCTUnwrap(Data(base64Encoded: base64)).write(to: url)
        return url
    }

    private func assertReadsBothEntries(_ base64: String, file: StaticString = #filePath, line: UInt = #line) throws {
        let url = try writeZip(base64)
        let entries = try MinimalZipReader.listEntries(at: url)

        XCTAssertEqual(entries.map(\.path), ["export/notes/a.json", "export/notes/b.txt"], file: file, line: line)
        XCTAssertEqual(entries.map(\.compressionMethod), [8, 0], file: file, line: line)
        XCTAssertEqual(entries.map(\.uncompressedSize), [Self.jsonEntry.utf8.count, Self.textEntry.utf8.count], file: file, line: line)
        XCTAssertEqual(entries[1].compressedSize, entries[1].uncompressedSize, file: file, line: line)

        let json = try MinimalZipReader.readEntryData(entries[0], from: url)
        XCTAssertEqual(String(decoding: json, as: UTF8.self), Self.jsonEntry, file: file, line: line)
        let text = try MinimalZipReader.readEntryData(entries[1], from: url)
        XCTAssertEqual(String(decoding: text, as: UTF8.self), Self.textEntry, file: file, line: line)
    }

    func testNormalArchive() throws {
        try assertReadsBothEntries(Self.normalBase64)
    }

    func testPythonForceZip64Archive() throws {
        try assertReadsBothEntries(Self.pythonZip64Base64)
    }

    func testZip64CentralDirectoryExtraFieldWithSentinels() throws {
        try assertReadsBothEntries(Self.zip64SentinelBase64)
    }

    func testZip64EndOfCentralDirectoryRecordAndLocator() throws {
        try assertReadsBothEntries(Self.zip64FullBase64)
    }

    func testCappedEntryCountWithoutZip64RecordsStillOpens() throws {
        try assertReadsBothEntries(Self.cappedCountBase64)
    }

    func testEncryptedEntryFailsWithHonestMessage() throws {
        let url = try writeZip(Self.encryptedBase64)
        let entries = try MinimalZipReader.listEntries(at: url)
        XCTAssertTrue(entries.allSatisfy(\.isEncrypted))
        XCTAssertThrowsError(try MinimalZipReader.readEntryData(entries[0], from: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .encryptedEntry)
            XCTAssertTrue(error.localizedDescription.contains("password-protected"))
        }
    }

    func testUnknownCompressionMethodFailsWithHonestMessage() throws {
        let url = try writeZip(Self.unknownMethodBase64)
        let entries = try MinimalZipReader.listEntries(at: url)
        XCTAssertThrowsError(try MinimalZipReader.readEntryData(entries[0], from: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .unsupportedCompressionMethod(12))
            XCTAssertTrue(error.localizedDescription.contains("compression method"))
        }
    }

    func testMultiDiskArchiveFailsWithHonestMessage() throws {
        let url = try writeZip(Self.multiDiskBase64)
        XCTAssertThrowsError(try MinimalZipReader.listEntries(at: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .multiDiskArchive)
            XCTAssertTrue(error.localizedDescription.contains("multiple disks"))
        }
    }

    func testCappedCountMultiDiskArchiveIsRejected() throws {
        let url = try writeZip(Self.cappedCountMultiDiskBase64)
        XCTAssertThrowsError(try MinimalZipReader.listEntries(at: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .multiDiskArchive)
        }
    }

    func testNotAZipFails() throws {
        let url = try writeZip(Data(repeating: 0x41, count: 64).base64EncodedString())
        XCTAssertThrowsError(try MinimalZipReader.listEntries(at: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .endOfCentralDirectoryNotFound)
        }
    }

    func testTruncatedZip64LocatorFailsInsteadOfMisreading() throws {
        // Drop the ZIP64 record + locator but keep the sentinel EOCD: must not guess.
        var data = try XCTUnwrap(Data(base64Encoded: Self.zip64FullBase64))
        let eocd = data.suffix(22)
        data = data.prefix(data.count - 22 - 20 - 56) + eocd
        let url = try writeZip(data.base64EncodedString())
        XCTAssertThrowsError(try MinimalZipReader.listEntries(at: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .invalidZip64Structure)
        }
    }
}
