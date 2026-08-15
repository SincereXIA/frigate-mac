import Foundation

enum RuntimeLayoutError: LocalizedError, Equatable {
    case missingResource(String)
    case invalidPort(Int)

    var errorDescription: String? {
        switch self {
        case .missingResource(let path):
            return "The bundled runtime resource is missing: \(path)"
        case .invalidPort(let port):
            return "The local web port is invalid: \(port)"
        }
    }
}

struct RuntimeLayout: Equatable, Sendable {
    let installDirectory: URL
    let pythonExecutable: URL
    let ffmpegExecutable: URL
    let ffprobeExecutable: URL
    let go2rtcExecutable: URL
    let nginxExecutable: URL
    let configDirectory: URL
    let cacheDirectory: URL
    let logDirectory: URL
    let runtimeDirectory: URL
    let labelmapPath: URL
    let audioLabelmapPath: URL
    let audioModelPath: URL
    let labelmapDirectory: URL

    static func bundled(
        bundle: Bundle = .main,
        fileManager: FileManager = .default
    ) throws -> RuntimeLayout {
        guard let resources = bundle.resourceURL else {
            throw RuntimeLayoutError.missingResource("Resources")
        }
        let runtime = resources.appendingPathComponent("Runtime", isDirectory: true)
        let applicationSupport = try fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Frigate", isDirectory: true)
        let caches = try fileManager.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Frigate", isDirectory: true)
        let logs = try fileManager.url(
            for: .libraryDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appendingPathComponent("Logs/Frigate", isDirectory: true)

        let layout = RuntimeLayout(
            installDirectory: runtime.appendingPathComponent("frigate", isDirectory: true),
            pythonExecutable: runtime.appendingPathComponent("python/bin/python3"),
            ffmpegExecutable: runtime.appendingPathComponent("bin/ffmpeg"),
            ffprobeExecutable: runtime.appendingPathComponent("bin/ffprobe"),
            go2rtcExecutable: runtime.appendingPathComponent("bin/go2rtc"),
            nginxExecutable: runtime.appendingPathComponent("bin/nginx"),
            configDirectory: applicationSupport.appendingPathComponent("config", isDirectory: true),
            cacheDirectory: caches.appendingPathComponent("cache", isDirectory: true),
            logDirectory: logs,
            runtimeDirectory: caches.appendingPathComponent("runtime", isDirectory: true),
            labelmapPath: runtime.appendingPathComponent("frigate/labelmap.txt"),
            audioLabelmapPath: runtime.appendingPathComponent("frigate/audio-labelmap.txt"),
            audioModelPath: runtime.appendingPathComponent("frigate/cpu_audio_model.tflite"),
            labelmapDirectory: runtime.appendingPathComponent("frigate/docker/main/rootfs/labelmap", isDirectory: true)
        )
        try layout.validateBundledResources(fileManager: fileManager)
        return layout
    }

    func validateBundledResources(fileManager: FileManager = .default) throws {
        let requiredFiles = [
            pythonExecutable,
            ffmpegExecutable,
            ffprobeExecutable,
            go2rtcExecutable,
            nginxExecutable,
            labelmapPath,
            audioLabelmapPath,
            audioModelPath,
            installDirectory.appendingPathComponent("web/dist/index.html"),
        ]
        for resource in requiredFiles where !fileManager.fileExists(atPath: resource.path) {
            throw RuntimeLayoutError.missingResource(resource.path)
        }
    }

    func prepareDirectories(fileManager: FileManager = .default) throws {
        for directory in [
            configDirectory,
            cacheDirectory,
            logDirectory,
            runtimeDirectory,
        ] {
            try fileManager.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
        }
    }

    func environment(
        mediaDirectory: URL,
        webPort: Int,
        jwtSecret: String,
        base: [String: String] = ProcessInfo.processInfo.environment
    ) throws -> [String: String] {
        guard (1...65535).contains(webPort) else {
            throw RuntimeLayoutError.invalidPort(webPort)
        }
        var values = base
        let runtimeEnvironment: [String: String] = [
            "FRIGATE_INSTALL_DIR": installDirectory.path,
            "FRIGATE_CONFIG_DIR": configDirectory.path,
            "FRIGATE_MEDIA_DIR": mediaDirectory.path,
            "FRIGATE_CACHE_DIR": cacheDirectory.path,
            "FRIGATE_LOG_DIR": logDirectory.path,
            "FRIGATE_RUNTIME_DIR": runtimeDirectory.path,
            "FRIGATE_LABELMAP_PATH": labelmapPath.path,
            "FRIGATE_AUDIO_LABELMAP_PATH": audioLabelmapPath.path,
            "FRIGATE_AUDIO_MODEL_PATH": audioModelPath.path,
            "FRIGATE_LABELMAP_DIR": labelmapDirectory.path,
            "FRIGATE_NATIVE_BIN_DIR": ffmpegExecutable.deletingLastPathComponent().path,
            "FRIGATE_FFMPEG_PATH": ffmpegExecutable.path,
            "FRIGATE_FFPROBE_PATH": ffprobeExecutable.path,
            "FRIGATE_GO2RTC_PATH": go2rtcExecutable.path,
            "FRIGATE_NGINX_PATH": nginxExecutable.path,
            "FRIGATE_NATIVE_PORT": String(webPort),
            "FRIGATE_JWT_SECRET": jwtSecret,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        ]
        values.merge(runtimeEnvironment) { _, newValue in newValue }
        return values
    }
}
