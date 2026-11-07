import SwiftUI
import UIKit

@main
struct USBDeckApp: App {
    @StateObject private var server = DeckServer()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            DeckView()
                .environmentObject(server)
                .preferredColorScheme(.dark)
                .statusBar(hidden: true)
                .onAppear {
                    server.start()
                    UIApplication.shared.isIdleTimerDisabled = true   // screen stays on
                }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active {
                UIApplication.shared.isIdleTimerDisabled = true
                server.refreshListener()
            }
        }
    }
}
