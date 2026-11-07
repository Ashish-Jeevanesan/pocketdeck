import SwiftUI

// Layout pushed from the Windows agent (config.json). The phone holds no actions,
// only what to draw — so you change buttons on the PC without reinstalling the app.

struct DeckConfig: Codable, Equatable {
    var title: String?
    var columns: Int?
    var columnsLandscape: Int?
    var pages: [DeckPage]

    enum CodingKeys: String, CodingKey {
        case title, columns, pages
        case columnsLandscape = "columns_landscape"
    }

    static let empty = DeckConfig(title: "USB Deck", columns: 3, columnsLandscape: nil, pages: [])
}

struct DeckPage: Codable, Equatable {
    var name: String
    var buttons: [DeckButton]
}

struct DeckButton: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var icon: String?
    var color: String?
}

// Wire messages (newline-delimited JSON)
struct Envelope: Decodable { let type: String }
struct ConfigMessage: Decodable { let config: DeckConfig }
struct ResultMessage: Decodable { let id: String; let ok: Bool; let message: String? }

extension Color {
    init(hex: String?, fallback: Color = Color(white: 0.22)) {
        guard var s = hex?.trimmingCharacters(in: .whitespaces), !s.isEmpty else { self = fallback; return }
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { self = fallback; return }
        self = Color(red: Double((v >> 16) & 0xFF) / 255,
                     green: Double((v >> 8) & 0xFF) / 255,
                     blue: Double(v & 0xFF) / 255)
    }
}
