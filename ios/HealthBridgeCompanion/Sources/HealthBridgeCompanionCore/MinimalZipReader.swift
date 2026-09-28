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
    }

    enum ZipError: Error, Equatable {
        case endOfCentralDirectoryNotFound
        case truncatedCentralDirectory
        case invalidLocalHeader
        case unsupportedCompressionMethod(UInt16)
        case decompressionFailed
    }

    private static let endOfCentralDirectorySignature: [UInt8] = [0x50, 0x4B, 0x05, 0x06]
    private static let centralDirectorySignature: [UInt8] = [0x50, 0x4B, 0x01, 0x02]
    private static let localFileHeaderSignature: [UInt8] = [0x50, 0x4B, 0x03, 0x04]

    /// Lists every entry in the zip's central directory (path, sizes, compression method,
    /// and where its local header lives -- everything needed to later read just that one
    /// entry with `readEntryData`). Does not read any entry's data.
    static func listEntries(at url: URL) throws -> [Entry] {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }

        let fileSize = try fileSize(of: handle)
        let eocd = try findEndOfCentralDirectory(handle: handle, fileSize: fileSize)
        let centralDirectoryOffset = Int(eocd.centralDirectoryOffset)
        let centralDirectorySize = Int(eocd.centralDirectorySize)

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
        let centralDirectoryOffset: UInt32
        let centralDirectorySize: UInt32
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
                let centralDirectorySize = readUInt32(tail, at: index + 12)
                let centralDirectoryOffset = readUInt32(tail, at: index + 16)
                return EndOfCentralDirectory(
                    centralDirectoryOffset: centralDirectoryOffset,
                    centralDirectorySize: centralDirectorySize
                )
            }
            index -= 1
        }
        throw ZipError.endOfCentralDirectoryNotFound
    }

    // MARK: - Central directory parsing

    private static func parseCentralDirectory(_ block: Data) throws -> [Entry] {
        var entries: [Entry] = []
        var offset = 0
        while offset + 46 <= block.count {
            guard Array(block[offset..<offset + 4]) == centralDirectorySignature else {
                break
            }
            let compressionMethod = readUInt16(block, at: offset + 10)
            let compressedSize = Int(readUInt32(block, at: offset + 20))
            let uncompressedSize = Int(readUInt32(block, at: offset + 24))
            let nameLength = Int(readUInt16(block, at: offset + 28))
            let extraLength = Int(readUInt16(block, at: offset + 30))
            let commentLength = Int(readUInt16(block, at: offset + 32))
            let localHeaderOffset = Int(readUInt32(block, at: offset + 42))
            let nameStart = offset + 46
            guard nameStart + nameLength <= block.count else {
                break
            }
            let nameData = block[nameStart..<nameStart + nameLength]
            let path = String(decoding: nameData, as: UTF8.self)

            entries.append(
                Entry(
                    path: path,
                    compressionMethod: compressionMethod,
                    compressedSize: compressedSize,
                    uncompressedSize: uncompressedSize,
                    localHeaderOffset: localHeaderOffset
                )
            )
            offset = nameStart + nameLength + extraLength + commentLength
        }
        return entries
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
