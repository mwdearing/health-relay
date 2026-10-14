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

    // MARK: - Declared sizes are refused before any read or allocation

    private func writeSyntheticArchive(_ members: [SyntheticZipFixture.Member]) throws -> URL {
        try writeZip(SyntheticZipFixture.bytes(for: members).base64EncodedString())
    }

    func testDeclaredUncompressedSizeOverCapIsRefusedBeforeReading() throws {
        let cap = UInt64(MinimalZipReader.maximumEntryUncompressedBytes)
        let url = try writeSyntheticArchive([
            .init(path: "clinical-records/big.json", declaredUncompressedSize: cap + 1),
        ])
        let entries = try MinimalZipReader.listEntries(at: url)
        XCTAssertEqual(entries.count, 1)
        XCTAssertThrowsError(try MinimalZipReader.readEntryData(entries[0], from: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .entryTooLarge)
        }
    }

    func testDeclaredCompressedSizeOverCapIsRefusedBeforeReading() throws {
        let cap = UInt64(MinimalZipReader.maximumEntryCompressedBytes)
        let url = try writeSyntheticArchive([
            .init(path: "clinical-records/big.json", declaredCompressedSize: cap + 1, declaredUncompressedSize: 2),
        ])
        let entries = try MinimalZipReader.listEntries(at: url)
        XCTAssertThrowsError(try MinimalZipReader.readEntryData(entries[0], from: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .entryTooLarge)
        }
    }

    func testDeclaredSizesNearUInt32MaxAreRefusedWithoutTrapping() throws {
        let nearMax = UInt64(UInt32.max) - 1
        let members: [SyntheticZipFixture.Member] = [
            .init(path: "clinical-records/a.json", declaredUncompressedSize: nearMax),
            .init(path: "clinical-records/b.json", declaredCompressedSize: nearMax),
        ]
        for member in members {
            let url = try writeSyntheticArchive([member])
            let entries = try MinimalZipReader.listEntries(at: url)
            XCTAssertThrowsError(try MinimalZipReader.readEntryData(entries[0], from: url)) { error in
                XCTAssertEqual(error as? MinimalZipReader.ZipError, .entryTooLarge)
            }
        }
    }

    func testDeclaredSizesNearUInt64MaxFailWithoutTrapping() throws {
        // ZIP64 values above Int.max cannot be represented as an entry size; the listing fails
        // with the existing ZIP64 error instead of converting them unchecked.
        let members: [SyntheticZipFixture.Member] = [
            .init(path: "clinical-records/a.json", declaredUncompressedSize: UInt64.max),
            .init(path: "clinical-records/b.json", declaredCompressedSize: UInt64.max),
        ]
        for member in members {
            let url = try writeSyntheticArchive([member])
            XCTAssertThrowsError(try MinimalZipReader.listEntries(at: url)) { error in
                XCTAssertEqual(error as? MinimalZipReader.ZipError, .invalidZip64Structure)
            }
        }
    }

    func testLocalHeaderOffsetNearIntMaxFailsWithoutTrapping() throws {
        // A declared offset this large is only reachable through a hand-built Entry; the
        // seek or the offset arithmetic must fail cleanly rather than trap on overflow.
        let url = try writeZip(Self.normalBase64)
        let hostile = MinimalZipReader.Entry(
            path: "clinical-records/hostile.json",
            compressionMethod: 0,
            compressedSize: 0,
            uncompressedSize: 0,
            localHeaderOffset: Int.max
        )
        XCTAssertThrowsError(try MinimalZipReader.readEntryData(hostile, from: url)) { error in
            XCTAssertEqual(error as? MinimalZipReader.ZipError, .invalidLocalHeader)
        }
    }

    func testSyntheticStoredMemberWithinCapsStillReadsItsBytes() throws {
        let payload = Array("{}".utf8)
        let url = try writeSyntheticArchive([
            .init(
                path: "clinical-records/ok.json",
                declaredCompressedSize: UInt64(payload.count),
                declaredUncompressedSize: UInt64(payload.count),
                payload: payload
            ),
        ])
        let entries = try MinimalZipReader.listEntries(at: url)
        let data = try MinimalZipReader.readEntryData(entries[0], from: url)
        XCTAssertEqual(data, Data(payload))
    }
}

/// Builds classic zip archives in memory from declared sizes, so tests can state sizes that the
/// reader must refuse without writing gigabytes. Sizes of 0xFFFF_FFFF or more are written as the
/// ZIP64 sentinel, with the real values in a 0x0001 extra field, as the zip format specifies.
enum SyntheticZipFixture {
    struct Member {
        let path: String
        var method: UInt16 = 0
        var declaredCompressedSize: UInt64 = 0
        var declaredUncompressedSize: UInt64 = 0
        var payload: [UInt8] = []
    }

    static func bytes(for members: [Member]) -> Data {
        var local: [UInt8] = []
        var central: [UInt8] = []
        for member in members {
            let name = Array(member.path.utf8)
            let localHeaderOffset = UInt64(local.count)
            let compressedIsZip64 = member.declaredCompressedSize >= 0xFFFF_FFFF
            let uncompressedIsZip64 = member.declaredUncompressedSize >= 0xFFFF_FFFF

            var zip64Values: [UInt64] = []
            if uncompressedIsZip64 {
                zip64Values.append(member.declaredUncompressedSize)
            }
            if compressedIsZip64 {
                zip64Values.append(member.declaredCompressedSize)
            }
            var extra: [UInt8] = []
            if !zip64Values.isEmpty {
                appendLittleEndian(0x0001, byteCount: 2, to: &extra)
                appendLittleEndian(UInt64(zip64Values.count * 8), byteCount: 2, to: &extra)
                for value in zip64Values {
                    appendLittleEndian(value, byteCount: 8, to: &extra)
                }
            }

            // Local file header (30 fixed bytes, then name, then data).
            appendLittleEndian(0x0403_4B50, byteCount: 4, to: &local)
            appendLittleEndian(20, byteCount: 2, to: &local)
            appendLittleEndian(0, byteCount: 2, to: &local)
            appendLittleEndian(UInt64(member.method), byteCount: 2, to: &local)
            appendLittleEndian(0, byteCount: 8, to: &local) // time, date, crc
            appendLittleEndian(UInt64(member.payload.count), byteCount: 4, to: &local)
            appendLittleEndian(UInt64(member.payload.count), byteCount: 4, to: &local)
            appendLittleEndian(UInt64(name.count), byteCount: 2, to: &local)
            appendLittleEndian(0, byteCount: 2, to: &local)
            local += name
            local += member.payload

            // Central directory header (46 fixed bytes, then name, extra field).
            appendLittleEndian(0x0201_4B50, byteCount: 4, to: &central)
            appendLittleEndian(20, byteCount: 2, to: &central)
            appendLittleEndian(20, byteCount: 2, to: &central)
            appendLittleEndian(0, byteCount: 2, to: &central)
            appendLittleEndian(UInt64(member.method), byteCount: 2, to: &central)
            appendLittleEndian(0, byteCount: 8, to: &central) // time, date, crc
            appendLittleEndian(compressedIsZip64 ? 0xFFFF_FFFF : member.declaredCompressedSize, byteCount: 4, to: &central)
            appendLittleEndian(uncompressedIsZip64 ? 0xFFFF_FFFF : member.declaredUncompressedSize, byteCount: 4, to: &central)
            appendLittleEndian(UInt64(name.count), byteCount: 2, to: &central)
            appendLittleEndian(UInt64(extra.count), byteCount: 2, to: &central)
            appendLittleEndian(0, byteCount: 2, to: &central) // comment length
            appendLittleEndian(0, byteCount: 2, to: &central) // disk number
            appendLittleEndian(0, byteCount: 2, to: &central) // internal attributes
            appendLittleEndian(0, byteCount: 4, to: &central) // external attributes
            appendLittleEndian(localHeaderOffset, byteCount: 4, to: &central)
            central += name
            central += extra
        }

        let centralDirectoryOffset = UInt64(local.count)
        let centralDirectorySize = UInt64(central.count)
        var archive = local + central
        // End of central directory record (22 bytes, no comment).
        appendLittleEndian(0x0605_4B50, byteCount: 4, to: &archive)
        appendLittleEndian(0, byteCount: 2, to: &archive)
        appendLittleEndian(0, byteCount: 2, to: &archive)
        appendLittleEndian(UInt64(members.count), byteCount: 2, to: &archive)
        appendLittleEndian(UInt64(members.count), byteCount: 2, to: &archive)
        appendLittleEndian(centralDirectorySize, byteCount: 4, to: &archive)
        appendLittleEndian(centralDirectoryOffset, byteCount: 4, to: &archive)
        appendLittleEndian(0, byteCount: 2, to: &archive)
        return Data(archive)
    }

    private static func appendLittleEndian(_ value: UInt64, byteCount: Int, to output: inout [UInt8]) {
        for index in 0..<byteCount {
            output.append(UInt8(truncatingIfNeeded: value >> UInt64(8 * index)))
        }
    }
}
