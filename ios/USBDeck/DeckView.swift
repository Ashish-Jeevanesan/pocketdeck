import SwiftUI
import UIKit

struct DeckView: View {
    @EnvironmentObject var server: DeckServer
    @State private var page = 0

    var body: some View {
        GeometryReader { geo in
            let landscape = geo.size.width > geo.size.height
            ZStack {
                Color.black.ignoresSafeArea()
                VStack(spacing: 6) {
                    header
                    if server.config.pages.isEmpty {
                        waiting
                    } else {
                        TabView(selection: $page) {
                            ForEach(Array(server.config.pages.enumerated()), id: \.offset) { idx, p in
                                PageGrid(page: p, columns: columns(landscape: landscape))
                                    .tag(idx)
                            }
                        }
                        .tabViewStyle(.page(indexDisplayMode: server.config.pages.count > 1 ? .always : .never))
                        .opacity(server.connected ? 1 : 0.35)
                        .allowsHitTesting(server.connected)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.top, 4)
            }
        }
        .onChange(of: server.config) { cfg in
            if page >= cfg.pages.count { page = 0 }
        }
    }

    private func columns(landscape: Bool) -> Int {
        let portrait = max(1, server.config.columns ?? 3)
        if landscape { return max(1, server.config.columnsLandscape ?? portrait + 2) }
        return portrait
    }

    private var header: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(server.connected ? Color.green : Color.red)
                .frame(width: 9, height: 9)
            Text(server.connected ? "USB connected" : "Plug in the cable")
                .font(.caption).foregroundColor(.gray)
            Spacer()
            Text(currentTitle)
                .font(.caption.weight(.semibold)).foregroundColor(.white.opacity(0.8))
        }
        .padding(.horizontal, 4)
    }

    private var currentTitle: String {
        let pages = server.config.pages
        guard !pages.isEmpty else { return server.config.title ?? "USB Deck" }
        return pages[min(page, pages.count - 1)].name
    }

    private var waiting: some View {
        VStack(spacing: 14) {
            Spacer()
            Image(systemName: "cable.connector")
                .font(.system(size: 54)).foregroundColor(.gray)
            Text("Connect to your PC with the USB cable\nand start the USB Deck agent.")
                .multilineTextAlignment(.center)
                .foregroundColor(.gray)
            if let err = server.lastError {
                Text(err).font(.caption2).foregroundColor(.red)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }
}

struct PageGrid: View {
    let page: DeckPage
    let columns: Int

    var body: some View {
        GeometryReader { geo in
            let spacing: CGFloat = 10
            let rows = max(1, Int(ceil(Double(page.buttons.count) / Double(columns))))
            let cellW = (geo.size.width - spacing * CGFloat(columns - 1)) / CGFloat(columns)
            // fit all rows on screen, leave room for the page dots
            let cellH = min(cellW, (geo.size.height - 28 - spacing * CGFloat(rows - 1)) / CGFloat(rows))
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: spacing), count: columns),
                      spacing: spacing) {
                ForEach(page.buttons) { b in
                    DeckKey(button: b).frame(height: max(60, cellH))
                }
            }
        }
    }
}

struct DeckKey: View {
    @EnvironmentObject var server: DeckServer
    let button: DeckButton
    @State private var pressed = false

    var body: some View {
        let flash = server.flashes[button.id]
        ZStack {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color(hex: button.color))
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(flash == nil ? Color.white.opacity(0.08) : (flash! ? Color.green : Color.red),
                        lineWidth: flash == nil ? 1 : 4)
            VStack(spacing: 6) {
                if let icon = button.icon, !icon.isEmpty {
                    Image(systemName: icon)
                        .font(.system(size: 28, weight: .semibold))
                }
                Text(button.label)
                    .font(.system(size: 13, weight: .semibold))
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.7)
            }
            .foregroundColor(.white)
            .padding(6)
        }
        .scaleEffect(pressed ? 0.93 : 1)
        .animation(.easeOut(duration: 0.08), value: pressed)
        .contentShape(Rectangle())
        // Fire on touch-DOWN (not release) for the lowest perceived latency.
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if !pressed {
                        pressed = true
                        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                        server.press(button.id)
                    }
                }
                .onEnded { _ in pressed = false }
        )
    }
}
