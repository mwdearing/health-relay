import Compression
import Foundation

/// A minimal, read-only ZIP central-directory reader (HealthRelay addition).
///
/// Written by hand instead of adding a third-party SPM dependency: hand-editing the
/// Xcode project's SPM package graph to make a `-target`-based `xcodebuild` build (this
/// project's CI invocation, not `-scheme`) actually resolve and link a package proved
/// unreliable without a real Xcode/macOS environment to verify the resolution against
/// (2026-09-28) -- three targeted fixes each surfaced a new "couldn't be opened because
/// there is no such file" error. This reader has no external dependency at all: only
/// `Foundation` (for `FileHandle`/`Data`) and `Compression` (Apple's own framework,
/// which implements the same raw DEFLATE ZIP entries use, confusingly under the
/// constant name `COMPRESSION_ZLIB` rather than a wrapped zlib stream).
///
/// Reads only the end-of-central-directory record, the central directory itself, and
/// -- one at a time, via `FileHandle` seeks -- the individual entries the caller asks
/// for. The whole zip is never loaded into memory or unpacked to disk; only the
/// central directory (small: one ~46-byte-plus-filename record per entry) and each
/// requested entry's own compressed bytes are read.
enum MinimalZipReader {
    struct Entry: Equatable {
        let path: String
        let compressionMethod: UInt16
        let compressedSize: Int
        let uncompressedSize: Int
        let localHeaderOffset: Int
        /// General-purpose flag bit 0: the entry is encrypted (not supported).
        let isEncrypted: Bool

        init(
            path: String,
            compressionMethod: UInt16,
            compressedSize: Int,
            uncompressedSize: Int,
            localHeaderOffset: Int,
            isEncrypted: Bool = false
        ) {
            self.path = path
            self.compressionMethod = compressionMethod
            self.compressedSize = compressedSize
            self.uncompressedSize = uncompressedSize
            self.localHeaderOffset = localHeaderOffset
            self.isEncrypted = isEncrypted
        }
    }

    enum ZipError: Error, Equatable {
        case endOfCentralDirectoryNotFound
        case truncatedCentralDirectory
        case invalidLocalHeader
        case unsupportedCompressionMethod(UInt16)
        case decompressionFailed
        case encryptedEntry
        case multiDiskArchive
        case invalidZip64Structure
    }

    private static let endOfCentralDirectorySignature: [UInt8] = [0x50, 0x4B, 0x05, 0x06]
    private static let centralDirectorySignature: [UInt8] = [0x50, 0x4B, 0x01, 0x02]
    private static let localFileHeaderSignature: [UInt8] = [0x50, 0x4B, 0x03, 0x04]
    private static let zip64LocatorSignature: [UInt8] = [0x50, 0x4B, 0x06, 0x07]
    private static let zip64EndOfCentralDirectorySignature: [UInt8] = [0x50, 0x4B, 0x06, 0x06]
    private static let sentinel32: UInt32 = 0xFFFF_FFFF
    private static let sentinel16: UInt16 = 0xFFFF

    /// Lists every entry in the zip's central directory (path, sizes, compression method,
    /// and where its local header lives -- everything needed to later read just that one
    /// entry with `readEntryData`). Does not read any entry's data.
    static func listEntries(at url: URL) throws -> [Entry] {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }

        let fileSize = try fileSize(of: handle)
        let eocd = try findEndOfCentralDirectory(handle: handle, fileSize: fileSize)
        let centralDirectoryOffset = eocd.centralDirectoryOffset
        let centralDirectorySize = eocd.centralDirectorySize
        guard centralDirectoryOffset >= 0, centralDirectorySize >= 0,
              centralDirectoryOffset <= fileSize,
              centralDirectorySize <= fileSize - centralDirectoryOffset
        else {
            throw ZipError.truncatedCentralDirectory
        }

        try handle.seek(toOffset: UInt64(centralDirectoryOffset))
        guard let block = try handle.read(upToCount: centralDirectorySize),
              block.count == centralDirectorySize
        else {
            throw ZipError.truncatedCentralDirectory
        }

        return try parseCentralDirectory(block)
    }

    /// Reads and decompresses one entry's data, given the `Entry` `listEntries` returned
    /// for it. Only that entry's bytes are read from disk.
    static func readEntryData(_ entry: Entry, from url: URL) throws -> Data {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }

        if entry.isEncrypted {
            throw ZipError.encryptedEntry
        }
        guard entry.compressionMethod == 0 || entry.compressionMethod == 8 else {
            throw ZipError.unsupportedCompressionMethod(entry.compressionMethod)
        }
        try handle.seek(toOffset: UInt64(entry.localHeaderOffset))
        guard let localHeader = try handle.read(upToCount: 30), localHeader.count == 30,
              Array(localHeader.prefix(4)) == localFileHeaderSignature
        else {
            throw ZipError.invalidLocalHeader
        }
        let nameLength = Int(readUInt16(localHeader, at: 26))
        let extraLength = Int(readUInt16(localHeader, at: 28))
        try handle.seek(toOffset: UInt64(entry.localHeaderOffset + 30 + nameLength + extraLength))

        guard let compressed = try handle.read(upToCount: entry.compressedSize),
              compressed.count == entry.compressedSize
        else {
            throw ZipError.truncatedCentralDirectory
        }

        switch entry.compressionMethod {
        case 0: // stored (no compression)
            return compressed
        case 8: // deflated
            return try inflateRawDeflate(compressed, expectedSize: entry.uncompressedSize)
        default:
            throw ZipError.unsupportedCompressionMethod(entry.compressionMethod)
        }
    }

    // MARK: - End of central directory

    private struct EndOfCentralDirectory {
        let centralDirectoryOffset: Int
        let centralDirectorySize: Int
    }

    /// Scans backward from the end of the file for the EOCD signature. Almost always in
    /// the fixed last 22 bytes (no zip comment); falls back to scanning the maximum
    /// possible comment length (65535 bytes) for a zip that has one.
    private static func findEndOfCentralDirectory(
        handle: FileHandle,
        fileSize: Int
    ) throws -> EndOfCentralDirectory {
        let maxCommentLength = 65535
        let searchWindow = min(fileSize, 22 + maxCommentLength)
        let searchStart = fileSize - searchWindow
        try handle.seek(toOffset: UInt64(searchStart))
        guard let tail = try handle.read(upToCount: searchWindow), tail.count >= 22 else {
            throw ZipError.endOfCentralDirectoryNotFound
        }

        var index = tail.count - 22
        while index >= 0 {
            if Array(tail[index..<index + 4]) == endOfCentralDirectorySignature {
                return try endOfCentralDirectory(
                    tail: tail,
                    eocdIndex: index,
                    eocdFileOffset: searchStart + index,
                    handle: handle,
                    fileSize: fileSize
                )
            }
            index -= 1
        }
        throw ZipError.endOfCentralDirectoryNotFound
    }

    /// Reads the classic EOCD at `eocdIndex` and, when any of its fields holds a ZIP64
    /// sentinel (0xFFFF / 0xFFFFFFFF), follows the ZIP64 locator (the 20 bytes right
    /// before the EOCD) to the ZIP64 EOCD record for the real 64-bit values.
    private static func endOfCentralDirectory(
        tail: Data,
        eocdIndex index: Int,
        eocdFileOffset: Int,
        handle: FileHandle,
        fileSize: Int
    ) throws -> EndOfCentralDirectory {
        let diskNumber = readUInt16(tail, at: index + 4)
        let centralDirectoryDisk = readUInt16(tail, at: index + 6)
        let entryCount = readUInt16(tail, at: index + 10)
        let size32 = readUInt32(tail, at: index + 12)
        let offset32 = readUInt32(tail, at: index + 16)

        let needsZip64 = entryCount == sentinel16 || size32 == sentinel32 || offset32 == sentinel32
        guard needsZip64 else {
            guard diskNumber == 0, centralDirectoryDisk == 0 else {
                throw ZipError.multiDiskArchive
            }
            return EndOfCentralDirectory(
                centralDirectoryOffset: Int(offset32),
                centralDirectorySize: Int(size32)
            )
        }

        // ZIP64 locator: 20 bytes immediately before the EOCD.
        var locatorData: Data?
        if eocdFileOffset >= 20 {
            try handle.seek(toOffset: UInt64(eocdFileOffset - 20))
            if let candidate = try handle.read(upToCount: 20), candidate.count == 20,
               Array(candidate.prefix(4)) == zip64LocatorSignature {
                locatorData = candidate
            }
        }
        guard let locator = locatorData else {
            // Only the entry count is capped (a classic archive with 65535 entries, or a
            // writer that caps it without ZIP64 records): size and offset are real, so
            // the classic values are still correct. A sentinel size/offset without a
            // locator is genuinely broken.
            guard size32 != sentinel32, offset32 != sentinel32 else {
                throw ZipError.invalidZip64Structure
            }
            return EndOfCentralDirectory(
                centralDirectoryOffset: Int(offset32),
                centralDirectorySize: Int(size32)
            )
        }
        let totalDisks = readUInt32(locator, at: 16)
        let recordOffset = readUInt64(locator, at: 8)
        guard totalDisks <= 1 else {
            throw ZipError.multiDiskArchive
        }
        guard recordOffset <= UInt64(fileSize) else {
            throw ZipError.invalidZip64Structure
        }

        // ZIP64 EOCD record: fixed 56-byte prefix (extensible data sector ignored).
        try handle.seek(toOffset: recordOffset)
        guard let record = try handle.read(upToCount: 56), record.count == 56,
              Array(record.prefix(4)) == zip64EndOfCentralDirectorySignature
        else {
            throw ZipError.invalidZip64Structure
        }
        guard readUInt32(record, at: 16) == 0, readUInt32(record, at: 20) == 0 else {
            throw ZipError.multiDiskArchive
        }
        let size64 = readUInt64(record, at: 40)
        let offset64 = readUInt64(record, at: 48)
        guard let size = Int(exactly: size64), let offset = Int(exactly: offset64) else {
            throw ZipError.invalidZip64Structure
        }
        return EndOfCentralDirectory(centralDirectoryOffset: offset, centralDirectorySize: size)
    }

    // MARK: - Central directory parsing

    private static func parseCentralDirectory(_ block: Data) throws -> [Entry] {
        var entries: [Entry] = []
        var offset = 0
        while offset + 46 <= block.count {
            guard Array(block[offset..<offset + 4]) == centralDirectorySignature else {
                break
            }
            let flags = readUInt16(block, at: offset + 8)
            let compressionMethod = readUInt16(block, at: offset + 10)
            var compressedSize = UInt64(readUInt32(block, at: offset + 20))
            var uncompressedSize = UInt64(readUInt32(block, at: offset + 24))
            let nameLength = Int(readUInt16(block, at: offset + 28))
            let extraLength = Int(readUInt16(block, at: offset + 30))
            let commentLength = Int(readUInt16(block, at: offset + 32))
            let diskStart = readUInt16(block, at: offset + 34)
            var localHeaderOffset = UInt64(readUInt32(block, at: offset + 42))
            let nameStart = offset + 46
            guard nameStart + nameLength + extraLength <= block.count else {
                break
            }
            let nameData = block[nameStart..<nameStart + nameLength]
            let path = String(decoding: nameData, as: UTF8.self)

            // ZIP64 extra field (id 0x0001): holds, in this fixed order, only those of
            // uncompressed size, compressed size, local header offset and disk number
            // whose 32-bit (16-bit for the disk) value is the 0xFFFFFFFF sentinel.
            var diskNumber = UInt32(diskStart)
            let needsZip64 = compressedSize == UInt64(sentinel32)
                || uncompressedSize == UInt64(sentinel32)
                || localHeaderOffset == UInt64(sentinel32)
                || diskStart == sentinel16
            if needsZip64 {
                let extraStart = nameStart + nameLength
                guard let field = zip64ExtraField(block, from: extraStart, length: extraLength) else {
                    throw ZipError.invalidZip64Structure
                }
                var cursor = field.lowerBound
                func next64() throws -> UInt64 {
                    guard cursor + 8 <= field.upperBound else {
                        throw ZipError.invalidZip64Structure
                    }
                    defer { cursor += 8 }
                    return readUInt64(block, at: cursor)
                }
                if uncompressedSize == UInt64(sentinel32) { uncompressedSize = try next64() }
                if compressedSize == UInt64(sentinel32) { compressedSize = try next64() }
                if localHeaderOffset == UInt64(sentinel32) { localHeaderOffset = try next64() }
                if diskStart == sentinel16 {
                    guard cursor + 4 <= field.upperBound else {
                        throw ZipError.invalidZip64Structure
                    }
                    diskNumber = readUInt32(block, at: cursor)
                }
            }
            guard diskNumber == 0 else {
                throw ZipError.multiDiskArchive
            }
            guard let compressed = Int(exactly: compressedSize),
                  let uncompressed = Int(exactly: uncompressedSize),
                  let headerOffset = Int(exactly: localHeaderOffset)
            else {
                throw ZipError.invalidZip64Structure
            }

            entries.append(
                Entry(
                    path: path,
                    compressionMethod: compressionMethod,
                    compressedSize: compressed,
                    uncompressedSize: uncompressed,
                    localHeaderOffset: headerOffset,
                    isEncrypted: flags & 0x0001 != 0
                )
            )
            offset = nameStart + nameLength + extraLength + commentLength
        }
        return entries
    }

    /// Finds the data range of the ZIP64 extended-information extra field (id 0x0001)
    /// inside an entry's extra-field block, or nil when absent or malformed.
    private static func zip64ExtraField(_ block: Data, from start: Int, length: Int) -> Range<Int>? {
        var cursor = start
        let end = start + length
        while cursor + 4 <= end {
            let id = readUInt16(block, at: cursor)
            let size = Int(readUInt16(block, at: cursor + 2))
            let dataStart = cursor + 4
            guard dataStart + size <= end else {
                return nil
            }
            if id == 0x0001 {
                return dataStart..<(dataStart + size)
            }
            cursor = dataStart + size
        }
        return nil
    }

    // MARK: - Little-endian reads

    private static func readUInt16(_ data: Data, at offset: Int) -> UInt16 {
        let base = data.startIndex + offset
        return UInt16(data[base]) | (UInt16(data[base + 1]) << 8)
    }

    private static func readUInt32(_ data: Data, at offset: Int) -> UInt32 {
        let base = data.startIndex + offset
        return UInt32(data[base])
            | (UInt32(data[base + 1]) << 8)
            | (UInt32(data[base + 2]) << 16)
            | (UInt32(data[base + 3]) << 24)
    }

    private static func readUInt64(_ data: Data, at offset: Int) -> UInt64 {
        UInt64(readUInt32(data, at: offset)) | (UInt64(readUInt32(data, at: offset + 4)) << 32)
    }

    private static func fileSize(of handle: FileHandle) throws -> Int {
        let current = try handle.offset()
        let end = try handle.seekToEnd()
        try handle.seek(toOffset: current)
        return Int(end)
    }

    // MARK: - Deflate

    /// ZIP's "deflated" method is raw DEFLATE (RFC 1951, no zlib/gzip wrapper). Apple's
    /// Compression framework implements exactly that under the constant
    /// `COMPRESSION_ZLIB` (a naming quirk of that framework, not a zlib-wrapped stream).
    private static func inflateRawDeflate(_ compressed: Data, expectedSize: Int) throws -> Data {
        guard expectedSize > 0 else {
            return Data()
        }
        var destination = Data(count: expectedSize)
        let decodedCount = destination.withUnsafeMutableBytes { destPtr -> Int in
            compressed.withUnsafeBytes { srcPtr -> Int in
                guard let destBase = destPtr.bindMemory(to: UInt8.self).baseAddress,
                      let srcBase = srcPtr.bindMemory(to: UInt8.self).baseAddress
                else {
                    return 0
                }
                return compression_decode_buffer(
                    destBase, expectedSize,
                    srcBase, compressed.count,
                    nil, COMPRESSION_ZLIB
                )
            }
        }
        guard decodedCount == expectedSize else {
            throw ZipError.decompressionFailed
        }
        return destination
    }
}

extension MinimalZipReader.ZipError: LocalizedError {
    var errorDescription: String? {
        switch self {
        case .endOfCentralDirectoryNotFound:
            return "This file does not look like a ZIP archive (no ZIP directory was found at its end)."
        case .truncatedCentralDirectory:
            return "The ZIP archive is incomplete or damaged: its directory or an entry's data is cut off."
        case .invalidLocalHeader:
            return "The ZIP archive is damaged: an entry's header is missing or unreadable."
        case let .unsupportedCompressionMethod(method):
            return "The ZIP archive uses a compression method (code \(method)) this reader does not support. Only stored and deflate entries can be read."
        case .decompressionFailed:
            return "An entry in the ZIP archive could not be decompressed; the archive may be damaged."
        case .encryptedEntry:
            return "The ZIP archive contains a password-protected (encrypted) entry, which this reader cannot open."
        case .multiDiskArchive:
            return "The ZIP archive is split across multiple disks or volumes, which this reader does not support."
        case .invalidZip64Structure:
            return "The ZIP archive's large-file (ZIP64) information is missing or inconsistent; the archive may be damaged."
        }
    }
}
