import Foundation
import Network
import ServiceManagement

@MainActor
final class LocalNetworkPermissionRequester {
    private var browser: NWBrowser?

    func request() {
        let descriptor = NWBrowser.Descriptor.bonjour(type: "_frigate._tcp", domain: nil)
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = true
        let browser = NWBrowser(for: descriptor, using: parameters)
        browser.stateUpdateHandler = { [weak self] state in
            if case .ready = state {
                Task { @MainActor in
                    self?.browser?.cancel()
                    self?.browser = nil
                }
            }
        }
        browser.start(queue: .main)
        self.browser = browser
    }
}

enum LaunchAtLoginController {
    static func setEnabled(_ enabled: Bool) throws {
        if enabled {
            if SMAppService.mainApp.status != .enabled {
                try SMAppService.mainApp.register()
            }
        } else if SMAppService.mainApp.status == .enabled {
            try SMAppService.mainApp.unregister()
        }
    }
}
