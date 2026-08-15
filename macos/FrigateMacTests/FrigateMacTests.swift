import Foundation
import XCTest
@testable import FrigateMac

final class FrigateMacTests: XCTestCase {
    func testSettingsStoreRoundTripAndPrivatePermissions() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("settings.json")
        let store = SettingsStore(fileURL: file)
        let expected = AppSettings(
            configPath: "/tmp/config.yml",
            mediaDirectory: "/tmp/media",
            mediaBookmark: Data("bookmark".utf8),
            launchAtLogin: true,
            webPort: 8971
        )

        try store.save(expected)

        XCTAssertEqual(try store.load(), expected)
        let attributes = try FileManager.default.attributesOfItem(atPath: file.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertFalse(String(decoding: try Data(contentsOf: file), as: UTF8.self).contains("JWT"))
    }

    func testRuntimeEnvironmentUsesExternalTLSPortAndBundledTools() throws {
        let root = URL(fileURLWithPath: "/Applications/Frigate.app/Contents/Resources/Runtime")
        let layout = RuntimeLayout(
            installDirectory: root.appendingPathComponent("frigate"),
            pythonExecutable: root.appendingPathComponent("python/bin/python3"),
            ffmpegExecutable: root.appendingPathComponent("bin/ffmpeg"),
            ffprobeExecutable: root.appendingPathComponent("bin/ffprobe"),
            go2rtcExecutable: root.appendingPathComponent("bin/go2rtc"),
            nginxExecutable: root.appendingPathComponent("bin/nginx"),
            configDirectory: URL(fileURLWithPath: "/tmp/config"),
            cacheDirectory: URL(fileURLWithPath: "/tmp/cache"),
            logDirectory: URL(fileURLWithPath: "/tmp/log"),
            runtimeDirectory: URL(fileURLWithPath: "/tmp/runtime"),
            labelmapPath: root.appendingPathComponent("frigate/labelmap.txt"),
            audioLabelmapPath: root.appendingPathComponent("frigate/audio-labelmap.txt"),
            audioModelPath: root.appendingPathComponent("frigate/cpu_audio_model.tflite"),
            labelmapDirectory: root.appendingPathComponent("frigate/labelmap")
        )

        let values = try layout.environment(
            mediaDirectory: URL(fileURLWithPath: "/tmp/media"),
            webPort: 8971,
            jwtSecret: "secret",
            base: [:]
        )

        XCTAssertEqual(values["FRIGATE_NATIVE_PORT"], "8971")
        XCTAssertEqual(values["FRIGATE_FFMPEG_PATH"], root.appendingPathComponent("bin/ffmpeg").path)
        XCTAssertEqual(values["FRIGATE_AUDIO_MODEL_PATH"], root.appendingPathComponent("frigate/cpu_audio_model.tflite").path)
        XCTAssertEqual(values["PYTHONNOUSERSITE"], "1")
        XCTAssertEqual(values["PYTHONDONTWRITEBYTECODE"], "1")
        XCTAssertEqual(values["FRIGATE_JWT_SECRET"], "secret")
    }

    func testSupervisorCommandUsesNativeBootstrapAndOptionalEnvironment() {
        let root = URL(fileURLWithPath: "/runtime")
        let layout = RuntimeLayout(
            installDirectory: root,
            pythonExecutable: root.appendingPathComponent("python"),
            ffmpegExecutable: root.appendingPathComponent("ffmpeg"),
            ffprobeExecutable: root.appendingPathComponent("ffprobe"),
            go2rtcExecutable: root.appendingPathComponent("go2rtc"),
            nginxExecutable: root.appendingPathComponent("nginx"),
            configDirectory: root,
            cacheDirectory: root,
            logDirectory: root,
            runtimeDirectory: root,
            labelmapPath: root,
            audioLabelmapPath: root,
            audioModelPath: root,
            labelmapDirectory: root
        )
        let configuration = SupervisorConfiguration(
            layout: layout,
            configFile: root.appendingPathComponent("config.yml"),
            environmentFile: root.appendingPathComponent(".env"),
            mediaDirectory: root,
            webPort: 8971,
            jwtSecret: "secret"
        )

        XCTAssertEqual(configuration.arguments(), [
            "-m", "frigate.runtime.bootstrap", "run",
            "--config", "/runtime/config.yml",
            "--env-file", "/runtime/.env",
        ])
        XCTAssertEqual(configuration.webURL.host, "127.0.0.1")
        XCTAssertEqual(configuration.webURL.scheme, "https")
        XCTAssertEqual(configuration.healthURL.absoluteString, "http://127.0.0.1:5001/version")
    }

    func testMissingMediaDirectoryIsRejectedBeforeLaunch() {
        let missing = URL(fileURLWithPath: "/Volumes/frigate-missing-\(UUID().uuidString)")
        let root = URL(fileURLWithPath: "/runtime")
        let layout = RuntimeLayout(
            installDirectory: root,
            pythonExecutable: root,
            ffmpegExecutable: root,
            ffprobeExecutable: root,
            go2rtcExecutable: root,
            nginxExecutable: root,
            configDirectory: root,
            cacheDirectory: root,
            logDirectory: root,
            runtimeDirectory: root,
            labelmapPath: root,
            audioLabelmapPath: root,
            audioModelPath: root,
            labelmapDirectory: root
        )
        let configuration = SupervisorConfiguration(
            layout: layout,
            configFile: root.appendingPathComponent("config.yml"),
            environmentFile: nil,
            mediaDirectory: missing,
            webPort: 8971,
            jwtSecret: "secret"
        )

        XCTAssertThrowsError(try configuration.validateMediaDirectory()) { error in
            XCTAssertEqual(
                error as? SupervisorError,
                .mediaDirectoryUnavailable(missing.path)
            )
        }
    }

    func testRestartPolicyIsBoundedAndRecoversAfterWindow() {
        var policy = RestartPolicy(maximumRestarts: 3, window: 60, delays: [1, 2, 4])
        let start = Date(timeIntervalSince1970: 1_000)

        XCTAssertEqual(policy.nextDelay(at: start), 1)
        XCTAssertEqual(policy.nextDelay(at: start.addingTimeInterval(1)), 2)
        XCTAssertEqual(policy.nextDelay(at: start.addingTimeInterval(2)), 4)
        XCTAssertNil(policy.nextDelay(at: start.addingTimeInterval(3)))
        XCTAssertEqual(policy.nextDelay(at: start.addingTimeInterval(61)), 2)
    }

    func testInvalidWebPortIsRejected() {
        let root = URL(fileURLWithPath: "/tmp")
        let layout = RuntimeLayout(
            installDirectory: root,
            pythonExecutable: root,
            ffmpegExecutable: root,
            ffprobeExecutable: root,
            go2rtcExecutable: root,
            nginxExecutable: root,
            configDirectory: root,
            cacheDirectory: root,
            logDirectory: root,
            runtimeDirectory: root,
            labelmapPath: root,
            audioLabelmapPath: root,
            audioModelPath: root,
            labelmapDirectory: root
        )

        XCTAssertThrowsError(try layout.environment(
            mediaDirectory: root,
            webPort: 0,
            jwtSecret: "secret",
            base: [:]
        ))
    }

    @MainActor
    func testSupervisorStopsAChildProcessCleanly() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let install = root.appendingPathComponent("install", isDirectory: true)
        let bin = root.appendingPathComponent("bin", isDirectory: true)
        let web = install.appendingPathComponent("web/dist", isDirectory: true)
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: web, withIntermediateDirectories: true)
        try "ok".write(to: web.appendingPathComponent("index.html"), atomically: true, encoding: .utf8)

        let child = bin.appendingPathComponent("python3")
        try "#!/bin/sh\ntrap 'exit 0' TERM INT\nwhile :; do /bin/sleep 1; done\n"
            .write(to: child, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: child.path)
        for name in ["ffmpeg", "ffprobe", "go2rtc", "nginx"] {
            let executable = bin.appendingPathComponent(name)
            try Data().write(to: executable)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        }
        let labelmap = root.appendingPathComponent("labelmap.txt")
        let audioLabelmap = root.appendingPathComponent("audio-labelmap.txt")
        let audioModel = root.appendingPathComponent("cpu_audio_model.tflite")
        try Data().write(to: labelmap)
        try Data().write(to: audioLabelmap)
        try Data().write(to: audioModel)
        let layout = RuntimeLayout(
            installDirectory: install,
            pythonExecutable: child,
            ffmpegExecutable: bin.appendingPathComponent("ffmpeg"),
            ffprobeExecutable: bin.appendingPathComponent("ffprobe"),
            go2rtcExecutable: bin.appendingPathComponent("go2rtc"),
            nginxExecutable: bin.appendingPathComponent("nginx"),
            configDirectory: root.appendingPathComponent("config"),
            cacheDirectory: root.appendingPathComponent("cache"),
            logDirectory: root.appendingPathComponent("logs"),
            runtimeDirectory: root.appendingPathComponent("run"),
            labelmapPath: labelmap,
            audioLabelmapPath: audioLabelmap,
            audioModelPath: audioModel,
            labelmapDirectory: root
        )
        let configFile = root.appendingPathComponent("config.yml")
        try Data().write(to: configFile)
        let mediaDirectory = root.appendingPathComponent("media", isDirectory: true)
        try FileManager.default.createDirectory(
            at: mediaDirectory,
            withIntermediateDirectories: true
        )
        let configuration = SupervisorConfiguration(
            layout: layout,
            configFile: configFile,
            environmentFile: nil,
            mediaDirectory: mediaDirectory,
            webPort: 65_000,
            jwtSecret: "secret"
        )
        let supervisor = ProcessSupervisor()

        await supervisor.start(configuration: configuration)
        XCTAssertEqual(supervisor.state, .starting)
        XCTAssertTrue(supervisor.isActive)
        await supervisor.stop(timeout: 2)
        XCTAssertEqual(supervisor.state, .stopped)
        XCTAssertFalse(supervisor.isActive)
    }
}
