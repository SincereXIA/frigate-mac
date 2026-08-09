import AppKit
import Combine
import CryptoKit
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var settings: AppSettings
    @Published var setupPresented: Bool
    @Published var errorMessage: String?

    let supervisor = ProcessSupervisor()
    private let settingsStore: SettingsStore
    private let secretStore: SecretStoring
    private let localNetwork = LocalNetworkPermissionRequester()
    private var cancellables: Set<AnyCancellable> = []
    private var didAttemptAutomaticStart = false

    init(
        settingsStore: SettingsStore? = nil,
        secretStore: SecretStoring = KeychainStore()
    ) {
        var resolvedStore: SettingsStore
        let loadedSettings: AppSettings
        do {
            if let settingsStore {
                resolvedStore = settingsStore
            } else {
                resolvedStore = try SettingsStore()
            }
            loadedSettings = try resolvedStore.load()
        } catch {
            let fallback = FileManager.default.temporaryDirectory
                .appendingPathComponent("Frigate-settings.json")
            resolvedStore = SettingsStore(fileURL: fallback)
            loadedSettings = AppSettings()
        }
        self.settingsStore = resolvedStore
        self.secretStore = secretStore
        settings = loadedSettings
        setupPresented = !loadedSettings.isConfigured
        supervisor.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
    }

    var webURL: URL {
        URL(string: "https://127.0.0.1:\(settings.webPort)")!
    }

    func chooseConfig() {
        let panel = NSOpenPanel()
        panel.title = "Choose a Frigate configuration"
        panel.allowedContentTypes = [.yaml]
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        settings.configPath = url.path
        persistSettings()
    }

    func chooseMediaDirectory() {
        let panel = NSOpenPanel()
        panel.title = "Choose a recording directory"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let url = panel.url else { return }
        settings.mediaDirectory = url.path
        settings.mediaBookmark = try? url.bookmarkData(
            options: .withSecurityScope,
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
        persistSettings()
    }

    func finishSetup() {
        guard settings.isConfigured else {
            errorMessage = "Choose both a configuration file and a recording directory"
            return
        }
        do {
            _ = try ensureJWTSecret()
            localNetwork.request()
            try settingsStore.save(settings)
            setupPresented = false
        } catch {
            NSLog("Frigate startup failed: %@", error.localizedDescription)
            errorMessage = error.localizedDescription
        }
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        do {
            try LaunchAtLoginController.setEnabled(enabled)
            settings.launchAtLogin = enabled
            try settingsStore.save(settings)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func start() async {
        do {
            guard let configPath = settings.configPath,
                  let mediaDirectory = resolvedMediaDirectory() else {
                setupPresented = true
                return
            }
            let layout = try RuntimeLayout.bundled()
            let secret = try ensureJWTSecret()
            let configURL = URL(fileURLWithPath: configPath)
            let envFile = configURL.deletingLastPathComponent().appendingPathComponent(".env")
            let configuration = SupervisorConfiguration(
                layout: layout,
                configFile: configURL,
                environmentFile: FileManager.default.fileExists(atPath: envFile.path) ? envFile : nil,
                mediaDirectory: mediaDirectory,
                webPort: settings.webPort,
                jwtSecret: secret
            )
            await supervisor.start(configuration: configuration)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func startAtLaunch() async {
        guard !didAttemptAutomaticStart else { return }
        didAttemptAutomaticStart = true
        if settings.isConfigured {
            await start()
        }
    }

    func stop() async {
        await supervisor.stop()
    }

    func openWebUI() {
        NSWorkspace.shared.open(webURL)
    }

    func revealLogs() {
        if let directory = try? FileManager.default.url(
            for: .libraryDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Logs/Frigate", isDirectory: true) {
            NSWorkspace.shared.open(directory)
        }
    }

    func quit() {
        NSApplication.shared.terminate(nil)
    }

    private func ensureJWTSecret() throws -> String {
        if let existing = try secretStore.read(account: "FRIGATE_JWT_SECRET") {
            return existing
        }
        let bytes = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
        let secret = bytes.base64EncodedString()
        try secretStore.save(secret, account: "FRIGATE_JWT_SECRET")
        return secret
    }

    private func resolvedMediaDirectory() -> URL? {
        if let bookmark = settings.mediaBookmark {
            var isStale = false
            if let bookmarked = try? URL(
                resolvingBookmarkData: bookmark,
                options: .withSecurityScope,
                relativeTo: nil,
                bookmarkDataIsStale: &isStale
            ) {
                _ = bookmarked.startAccessingSecurityScopedResource()
                return bookmarked
            }
        }
        guard let path = settings.mediaDirectory else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    private func persistSettings() {
        do {
            try settingsStore.save(settings)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
