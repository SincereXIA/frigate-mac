import Combine
import Darwin
import Foundation

enum SupervisorState: Equatable, Sendable {
    case stopped
    case starting
    case running
    case stopping
    case failed(String)

    var title: String {
        switch self {
        case .stopped: return "Stopped"
        case .starting: return "Starting"
        case .running: return "Running"
        case .stopping: return "Stopping"
        case .failed: return "Needs attention"
        }
    }
}

struct SupervisorConfiguration: Equatable, Sendable {
    let layout: RuntimeLayout
    let configFile: URL
    let environmentFile: URL?
    let mediaDirectory: URL
    let webPort: Int
    let jwtSecret: String

    var webURL: URL {
        URL(string: "https://127.0.0.1:\(webPort)")!
    }

    var healthURL: URL {
        URL(string: "http://127.0.0.1:5001/version")!
    }

    func arguments() -> [String] {
        var values = [
            "-m", "frigate.runtime.bootstrap", "run",
            "--config", configFile.path,
        ]
        if let environmentFile {
            values += ["--env-file", environmentFile.path]
        }
        return values
    }
}

@MainActor
final class ProcessSupervisor: ObservableObject {
    @Published private(set) var state: SupervisorState = .stopped
    @Published private(set) var lastExitStatus: Int32?

    private var process: Process?
    private var desiredRunning = false
    private var restartPolicy = RestartPolicy()
    private var configuration: SupervisorConfiguration?
    private var outputPipe: Pipe?
    private var logWriter: RotatingLogWriter?
    private var healthTask: Task<Void, Never>?

    var isActive: Bool {
        switch state {
        case .stopped, .failed: return false
        case .starting, .running, .stopping: return true
        }
    }

    func start(configuration: SupervisorConfiguration) async {
        guard process == nil else { return }
        desiredRunning = true
        self.configuration = configuration
        restartPolicy.reset()
        await launch(configuration: configuration)
    }

    func stop(timeout: TimeInterval = 20) async {
        desiredRunning = false
        healthTask?.cancel()
        healthTask = nil
        guard let process else {
            state = .stopped
            return
        }
        state = .stopping
        process.terminate()
        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if process.isRunning {
            kill(process.processIdentifier, SIGKILL)
            while process.isRunning {
                try? await Task.sleep(nanoseconds: 50_000_000)
            }
        }
        clearProcessResources()
        state = .stopped
    }

    private func launch(configuration: SupervisorConfiguration) async {
        state = .starting
        do {
            try configuration.layout.prepareDirectories()
            try configuration.layout.validateBundledResources()
            let child = Process()
            child.executableURL = configuration.layout.pythonExecutable
            child.arguments = configuration.arguments()
            child.currentDirectoryURL = configuration.layout.installDirectory
            child.environment = try configuration.layout.environment(
                mediaDirectory: configuration.mediaDirectory,
                webPort: configuration.webPort,
                jwtSecret: configuration.jwtSecret
            )
            let pipe = Pipe()
            child.standardOutput = pipe
            child.standardError = pipe
            let writer = RotatingLogWriter(
                fileURL: configuration.layout.logDirectory.appendingPathComponent("supervisor.log")
            )
            pipe.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                if !data.isEmpty { writer.append(data) }
            }
            child.terminationHandler = { [weak self] terminated in
                Task { @MainActor in
                    await self?.handleTermination(status: terminated.terminationStatus)
                }
            }
            process = child
            outputPipe = pipe
            logWriter = writer
            try child.run()
            healthTask = Task { [weak self] in
                await self?.monitorHealth(configuration: configuration)
            }
        } catch {
            clearProcessResources()
            state = .failed(error.localizedDescription)
        }
    }

    private func monitorHealth(configuration: SupervisorConfiguration) async {
        let deadline = Date().addingTimeInterval(60)
        let healthURL = configuration.healthURL
        while !Task.isCancelled && Date() < deadline {
            guard process?.isRunning == true else { return }
            var request = URLRequest(url: healthURL)
            request.timeoutInterval = 2
            do {
                let (_, response) = try await URLSession.shared.data(for: request)
                if let response = response as? HTTPURLResponse,
                   (200..<500).contains(response.statusCode) {
                    state = .running
                    return
                }
            } catch {
                // Startup health checks are retried until the deadline.
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        if !Task.isCancelled && process?.isRunning == true {
            let message = "Frigate did not become healthy within 60 seconds"
            await stop()
            state = .failed(message)
        }
    }

    private func handleTermination(status: Int32) async {
        lastExitStatus = status
        clearProcessResources()
        guard desiredRunning, let configuration else {
            state = .stopped
            return
        }
        guard let delay = restartPolicy.nextDelay() else {
            desiredRunning = false
            state = .failed("Frigate exited repeatedly with status \(status)")
            return
        }
        state = .failed("Frigate exited with status \(status); restarting")
        try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        guard desiredRunning else { return }
        await launch(configuration: configuration)
    }

    private func clearProcessResources() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        outputPipe = nil
        process = nil
        logWriter = nil
    }
}
