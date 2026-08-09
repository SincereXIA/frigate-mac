import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var model: AppModel? {
        didSet { startIfReady() }
    }
    private var terminationApproved = false
    private var didFinishLaunching = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        didFinishLaunching = true
        startIfReady()
    }

    private func startIfReady() {
        guard didFinishLaunching, let model else { return }
        Task { @MainActor in
            await model.startAtLaunch()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminationApproved || model?.supervisor.isActive != true {
            return .terminateNow
        }
        Task {
            await model?.stop()
            terminationApproved = true
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
struct FrigateMacApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model: AppModel

    init() {
        let model = AppModel()
        _model = StateObject(wrappedValue: model)
        appDelegate.model = model
    }

    var body: some Scene {
        MenuBarExtra("Frigate", systemImage: statusIcon) {
            VStack(alignment: .leading, spacing: 10) {
                Label(model.supervisor.state.title, systemImage: statusIcon)
                Divider()
                Button("Open Frigate") { model.openWebUI() }
                    .disabled(model.supervisor.state != .running)
                if model.supervisor.isActive {
                    Button("Stop Frigate") { Task { await model.stop() } }
                } else {
                    Button("Start Frigate") { Task { await model.start() } }
                }
                Button("Setup") { model.setupPresented = true }
                Button("Show Logs") { model.revealLogs() }
                Divider()
                Button("Quit Frigate") { model.quit() }
            }
            .padding(12)
            .frame(width: 220)
            .task {
                appDelegate.model = model
            }
            .sheet(isPresented: $model.setupPresented) {
                SetupView(model: model)
            }
            .alert("Frigate", isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { if !$0 { model.errorMessage = nil } }
            )) {
                Button("OK") { model.errorMessage = nil }
            } message: {
                Text(model.errorMessage ?? "")
            }
        }
        .menuBarExtraStyle(.window)
    }

    private var statusIcon: String {
        switch model.supervisor.state {
        case .running: return "video.fill"
        case .starting, .stopping: return "hourglass"
        case .stopped: return "video.slash"
        case .failed: return "exclamationmark.triangle"
        }
    }
}

private struct SetupView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Set up Frigate")
                .font(.title2)
            Text("Choose your existing Frigate configuration and where recordings should be stored.")
                .foregroundStyle(.secondary)
            pathRow(title: "Configuration", path: model.settings.configPath) {
                model.chooseConfig()
            }
            pathRow(title: "Recordings", path: model.settings.mediaDirectory) {
                model.chooseMediaDirectory()
            }
            Toggle(
                "Open at login",
                isOn: Binding(
                    get: { model.settings.launchAtLogin },
                    set: { model.setLaunchAtLogin($0) }
                )
            )
            HStack {
                Spacer()
                Button("Finish") { model.finishSetup() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!model.settings.isConfigured)
            }
        }
        .padding(24)
        .frame(width: 560)
    }

    private func pathRow(
        title: String,
        path: String?,
        action: @escaping () -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            HStack {
                Text(path ?? "Not selected")
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(path == nil ? .secondary : .primary)
                Spacer()
                Button("Choose", action: action)
            }
        }
    }
}
