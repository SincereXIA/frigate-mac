import Foundation

final class RotatingLogWriter {
    private let fileURL: URL
    private let maximumBytes: UInt64
    private let fileManager: FileManager
    private let lock = NSLock()

    init(
        fileURL: URL,
        maximumBytes: UInt64 = 5 * 1024 * 1024,
        fileManager: FileManager = .default
    ) {
        self.fileURL = fileURL
        self.maximumBytes = maximumBytes
        self.fileManager = fileManager
    }

    func append(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        do {
            try fileManager.createDirectory(
                at: fileURL.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try rotateIfNeeded(incomingBytes: UInt64(data.count))
            if !fileManager.fileExists(atPath: fileURL.path) {
                fileManager.createFile(atPath: fileURL.path, contents: nil)
                try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
            }
            let handle = try FileHandle(forWritingTo: fileURL)
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
            try handle.close()
        } catch {
            // Logging must not bring down the supervisor.
        }
    }

    private func rotateIfNeeded(incomingBytes: UInt64) throws {
        let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path)
        let current = (attributes?[.size] as? NSNumber)?.uint64Value ?? 0
        guard current + incomingBytes > maximumBytes else { return }
        let previous = fileURL.appendingPathExtension("1")
        if fileManager.fileExists(atPath: previous.path) {
            try fileManager.removeItem(at: previous)
        }
        if fileManager.fileExists(atPath: fileURL.path) {
            try fileManager.moveItem(at: fileURL, to: previous)
        }
    }
}
