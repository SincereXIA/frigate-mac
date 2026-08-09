import Foundation

struct AppSettings: Codable, Equatable, Sendable {
    var configPath: String?
    var mediaDirectory: String?
    var mediaBookmark: Data?
    var launchAtLogin = false
    var webPort = 8971

    var isConfigured: Bool {
        guard let configPath, let mediaDirectory else { return false }
        return !configPath.isEmpty && !mediaDirectory.isEmpty
    }
}

final class SettingsStore {
    private let fileURL: URL
    private let fileManager: FileManager
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(fileURL: URL, fileManager: FileManager = .default) {
        self.fileURL = fileURL
        self.fileManager = fileManager
        encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        decoder = JSONDecoder()
    }

    convenience init(fileManager: FileManager = .default) throws {
        let directory = try fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Frigate", isDirectory: true)
        self.init(fileURL: directory.appendingPathComponent("settings.json"), fileManager: fileManager)
    }

    func load() throws -> AppSettings {
        guard fileManager.fileExists(atPath: fileURL.path) else {
            return AppSettings()
        }
        return try decoder.decode(AppSettings.self, from: Data(contentsOf: fileURL))
    }

    func save(_ settings: AppSettings) throws {
        try fileManager.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let data = try encoder.encode(settings)
        try data.write(to: fileURL, options: .atomic)
        try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
    }
}
