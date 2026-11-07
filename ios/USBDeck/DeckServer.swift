import Foundation
import Darwin
import UIKit

/// Listens on 127.0.0.1:2345 ONLY.
///
/// Loopback is not reachable from Wi-Fi or any network. The only way in is Apple's
/// USB multiplexer (usbmuxd), which tunnels a TCP connection from the PC over the
/// Lightning cable. So: no cable, no deck. Same mechanism Xcode / Elgato use.
final class DeckServer: ObservableObject {
    static let port: UInt16 = 2345

    @Published var connected = false
    @Published var config: DeckConfig
    @Published var flashes: [String: Bool] = [:]   // button id -> ok?
    @Published var lastError: String?

    private let lock = NSLock()
    private var clientFD: Int32 = -1
    private var listenFD: Int32 = -1
    private var started = false
    private let writeQueue = DispatchQueue(label: "usbdeck.write")
    private static let configKey = "usbdeck.lastConfig"

    private let deviceName = UIDevice.current.name   // read once on the main thread

    init() {
        if let data = UserDefaults.standard.data(forKey: Self.configKey),
           let saved = try? JSONDecoder().decode(DeckConfig.self, from: data) {
            config = saved
        } else {
            config = .empty
        }
    }

    // MARK: lifecycle

    func start() {
        guard !started else { return }
        started = true
        let t = Thread { [weak self] in self?.acceptLoop() }
        t.name = "usbdeck.accept"
        t.start()
    }

    /// iOS may silently kill sockets while the app is suspended. Called when the app
    /// becomes active: knocks the listener over so the accept loop rebuilds it.
    func refreshListener() {
        lock.lock()
        let fd = listenFD
        lock.unlock()
        if fd >= 0 { Darwin.shutdown(fd, SHUT_RDWR) }
    }

    private func acceptLoop() {
        while true {
            let lfd = makeListener()
            if lfd < 0 { sleep(1); continue }
            lock.lock(); listenFD = lfd; lock.unlock()

            while true {
                let fd = Darwin.accept(lfd, nil, nil)
                if fd < 0 {
                    if errno == EINTR { continue }
                    break                       // listener dead -> rebuild
                }
                serveClient(fd)                 // one PC at a time
            }
            lock.lock(); listenFD = -1; lock.unlock()
            Darwin.close(lfd)
            usleep(300_000)
        }
    }

    private func makeListener() -> Int32 {
        let fd = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        if fd < 0 { return -1 }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = Self.port.bigEndian
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))   // loopback only

        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        if bound != 0 || Darwin.listen(fd, 1) != 0 {
            let msg = String(cString: strerror(errno))
            DispatchQueue.main.async { self.lastError = "Listener: \(msg)" }
            Darwin.close(fd)
            return -1
        }
        DispatchQueue.main.async { self.lastError = nil }
        return fd
    }

    private func serveClient(_ fd: Int32) {
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &yes, socklen_t(MemoryLayout<Int32>.size))
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &yes, socklen_t(MemoryLayout<Int32>.size))

        lock.lock(); clientFD = fd; lock.unlock()
        DispatchQueue.main.async { self.connected = true }
        transmit(["type": "hello", "app": "USBDeck", "version": "1.0",
                  "device": deviceName])

        var buffer = Data()
        let capacity = 64 * 1024
        var chunk = [UInt8](repeating: 0, count: capacity)
        while true {
            let n = Darwin.recv(fd, &chunk, capacity, 0)
            if n <= 0 {
                if n < 0 && errno == EINTR { continue }
                break
            }
            buffer.append(contentsOf: chunk[0..<n])
            while let nl = buffer.firstIndex(of: 0x0A) {
                let line = buffer.subdata(in: buffer.startIndex..<nl)
                buffer.removeSubrange(buffer.startIndex...nl)
                if !line.isEmpty { handle(line) }
            }
        }

        lock.lock(); clientFD = -1; lock.unlock()
        Darwin.close(fd)
        DispatchQueue.main.async { self.connected = false }
    }

    // MARK: protocol

    private func handle(_ line: Data) {
        let dec = JSONDecoder()
        guard let env = try? dec.decode(Envelope.self, from: line) else { return }
        switch env.type {
        case "config":
            guard let msg = try? dec.decode(ConfigMessage.self, from: line) else { return }
            if let data = try? JSONEncoder().encode(msg.config) {
                UserDefaults.standard.set(data, forKey: Self.configKey)
            }
            DispatchQueue.main.async { self.config = msg.config }
        case "result":
            guard let msg = try? dec.decode(ResultMessage.self, from: line) else { return }
            DispatchQueue.main.async {
                self.flashes[msg.id] = msg.ok
                if !msg.ok { UINotificationFeedbackGenerator().notificationOccurred(.error) }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                    self.flashes[msg.id] = nil
                }
            }
        case "ping":
            transmit(["type": "pong"])
        default:
            break
        }
    }

    func press(_ id: String) {
        transmit(["type": "press", "id": id])
    }

    private func transmit(_ obj: [String: Any]) {
        guard var data = try? JSONSerialization.data(withJSONObject: obj) else { return }
        data.append(0x0A)
        writeQueue.async { [weak self] in
            guard let self = self else { return }
            self.lock.lock(); let fd = self.clientFD; self.lock.unlock()
            guard fd >= 0 else { return }
            data.withUnsafeBytes { raw in
                guard let base = raw.baseAddress else { return }
                var sent = 0
                while sent < raw.count {
                    let n = Darwin.send(fd, base + sent, raw.count - sent, 0)
                    if n <= 0 { if errno == EINTR { continue }; return }
                    sent += n
                }
            }
        }
    }
}
