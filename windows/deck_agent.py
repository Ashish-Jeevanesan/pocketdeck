"""USB Deck - Windows agent.

Waits for the iPhone on the USB cable, pushes the button layout from config.json,
and runs the action when a button is pressed. Edit config.json while it runs and
the phone updates instantly.

    py deck_agent.py            (uses config.json next to this file)
    py deck_agent.py my.json
"""
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import actions
import icons
import usbmux
from deck_state import STATE

PHONE_PORT = 2345
PING_EVERY = 2.0
DEAD_AFTER = 7.0

_last_status = None


def status(msg):
    """iPhone transport status (printed only when it changes)."""
    global _last_status
    STATE.iphone = msg
    if msg != _last_status:
        log(msg)
        _last_status = msg


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    STATE.add_event(line)


def execute(config, bid, source, choice=None):
    """Run one button press (any transport).
    Returns dict(ok, message, ms) plus ask=<label> when the app is already open and the
    tile wants the user to pick 'switch' or 'new' (the phone then calls again with choice)."""
    t0 = time.perf_counter()
    ok, message, ask = True, "", None
    btn = config.buttons.get(bid)
    try:
        if STATE.paused:
            raise actions.ActionError("Deck is paused - tap Resume")
        if btn is None:
            raise actions.ActionError("Unknown button '%s'" % bid)
        actions.run_action(btn["action"], config.profiles, choice)
    except actions.NeedsChoice:
        ok, ask = False, (btn.get("label") or bid)
    except Exception as e:  # every failure goes back to the phone (red flash)
        ok, message = False, str(e)
    ms = (time.perf_counter() - t0) * 1000
    status_txt = "ASK" if ask else ("OK " if ok else "ERR")
    log("  [%s] %-14s %s  (%.0f ms)%s%s" % (source, bid, status_txt, ms, "  " + message if message else "",
                                            "  (%s)" % choice if choice else ""))
    out = {"ok": ok, "message": message, "ms": round(ms)}
    if ask:
        out["ask"] = ask
    return out


# --------------------------------------------------------------------------- config
# Android/web deck can't use Apple's SF Symbols, so map the common ones to emoji.
# A button can also set "emoji" directly in config.json.
EMOJI = [
    ("mic", "🎙️"), ("video", "📹"), ("rectangle.on.rectangle", "🖥️"), ("hand", "✋"),
    ("bubble", "💬"), ("phone.down", "📞"), ("folder", "📁"), ("arrow.down", "⬇️"),
    ("desktopcomputer", "🖥️"), ("server", "🗄️"), ("envelope", "✉️"), ("tablecells", "📊"),
    ("globe", "🌐"), ("doc", "📝"), ("plus.forwardslash.minus", "🧮"), ("gauge", "📈"),
    ("speaker.slash", "🔇"), ("speaker.wave.1", "🔉"), ("speaker.wave.3", "🔊"),
    ("playpause", "⏯️"), ("lock", "🔒"), ("camera.viewfinder", "✂️"), ("camera", "📷"),
    ("terminal", "⌨️"), ("gear", "⚙️"), ("calendar", "📅"), ("star", "⭐"),
]


def emoji_for(icon):
    if not icon:
        return ""
    for key, e in EMOJI:
        if icon.startswith(key):
            return e
    return "🔘"


class Config:
    def __init__(self, path):
        self.path = path
        self.mtime = 0
        self.data = None
        self.buttons = {}
        self.load()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.data, self.buttons = data, self.validate(data)
        self.mtime = os.path.getmtime(self.path)
        actions.DEFAULT_WINDOW = data.get("open_window", "maximize")
        actions.DEFAULT_IF_OPEN = data.get("if_open", "ask")

    @staticmethod
    def validate(data):
        if not isinstance(data.get("pages"), list):
            raise ValueError("config needs a 'pages' list")
        buttons = {}
        for page in data.get("pages", []):
            for b in page.get("buttons", []):
                bid = b.get("id")
                if not bid:
                    raise ValueError("A button on page '%s' has no id" % page.get("name"))
                if bid in buttons:
                    raise ValueError("Duplicate button id '%s'" % bid)
                if "action" not in b:
                    raise ValueError("Button '%s' has no action" % bid)
                if not isinstance(b["action"], dict) or not b["action"].get("type"):
                    raise ValueError("Button '%s' needs an action type" % bid)
                buttons[bid] = b
        return buttons

    def save(self, data):
        """Validate, back up the old file, write atomically, reload. Raises ValueError."""
        self.validate(data)
        with self._reload_lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if os.path.exists(self.path):
                try:
                    os.replace(self.path, self.path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, self.path)
            self.load()
        log("Layout saved from the editor (%d buttons)." % len(self.buttons))

    def changed(self):
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return False

    _reload_lock = threading.Lock()

    def maybe_reload(self):
        """Reload if config.json was saved. Thread-safe. Returns True if a new layout loaded."""
        with self._reload_lock:
            if not self.changed():
                return False
            try:
                self.load()
                log("config.json reloaded.")
                return True
            except Exception as e:
                self.mtime = os.path.getmtime(self.path)
                log("config.json has an error, keeping old layout: %s" % e)
                return False

    def version(self):
        """Changes when config.json is saved or new tile icons become available."""
        gen = icons.MANAGER.generation if icons.MANAGER else 0
        return "%s:%s" % (self.mtime, gen)

    def phone_layout(self):
        """What the phone needs to draw - no actions/paths leave the PC."""
        d = self.data
        mgr = icons.MANAGER

        def tile(b):
            t = {k: b.get(k) for k in ("id", "label", "icon", "color")}
            t["emoji"] = b.get("emoji") or emoji_for(b.get("icon"))
            t["image"] = mgr.url_for(b, self.profiles) if mgr else None
            return t

        return {
            "title": d.get("title", "USB Deck"),
            "columns": d.get("columns", 3),
            "columns_landscape": d.get("columns_landscape"),
            "rows": d.get("rows"),                       # None = fit as many rows as the screen allows
            "rows_landscape": d.get("rows_landscape"),
            "version": self.version(),
            "pages": [{"name": p.get("name", "Page"), "buttons": [tile(b) for b in p.get("buttons", [])]}
                      for p in d.get("pages", [])],
        }

    @property
    def profiles(self):
        return self.data.get("focus_profiles", {})


# --------------------------------------------------------------------------- session
class Session:
    def __init__(self, sock, config, pool):
        self.sock = sock
        self.config = config
        self.pool = pool
        self.send_lock = threading.Lock()
        self.alive = True

    def send(self, obj):
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
        with self.send_lock:
            self.sock.sendall(data)

    def send_config(self):
        self.sent_version = self.config.mtime
        self.send({"type": "config", "config": self.config.phone_layout()})

    def on_press(self, bid, t_rx):
        r = execute(self.config, bid, "iphone", "noask")
        ok, message = r["ok"], r["message"]
        if self.alive:
            try:
                self.send({"type": "result", "id": bid, "ok": ok, "message": message})
            except OSError:
                pass

    def run(self):
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(0.25)
        self.send_config()
        buf = b""
        last_rx = last_ping = time.monotonic()
        try:
            while True:
                try:
                    chunk = self.sock.recv(65536)
                    if not chunk:
                        break
                    last_rx = time.monotonic()
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            self.handle(line, time.perf_counter())
                except socket.timeout:
                    pass
                now = time.monotonic()
                if now - last_ping >= PING_EVERY:
                    self.send({"type": "ping"})
                    last_ping = now
                if now - last_rx > DEAD_AFTER:
                    log("Phone stopped responding.")
                    break
                if self.config.maybe_reload() or self.sent_version != self.config.mtime:
                    self.send_config()
        except OSError as e:
            log("Connection lost: %s" % e)
        finally:
            self.alive = False
            self.sock.close()

    def handle(self, line, t_rx):
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            return
        kind = msg.get("type")
        if kind == "press":
            self.pool.submit(self.on_press, msg.get("id"), t_rx)
        elif kind == "hello":
            log("Phone says hello: %s (app %s)" % (msg.get("device"), msg.get("version")))


# --------------------------------------------------------------------------- main
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "config.json")
    example = os.path.join(here, "config.example.json")
    if not os.path.exists(path) and os.path.exists(example):     # first run from a fresh clone
        import shutil
        shutil.copyfile(example, path)
    try:
        config = Config(path)
    except Exception as e:
        print("Could not load %s: %s" % (path, e))
        sys.exit(1)
    log("USB Deck agent - %d buttons loaded from %s" % (len(config.buttons), path))
    pool = ThreadPoolExecutor(max_workers=4)
    wanted_serial = config.data.get("device_serial")

    STATE.write_token(here)
    icons.manager(log)
    import media
    media.manager(log)
    android = config.data.get("android", {})
    if android.get("enabled", True):
        import web_deck
        try:
            web_deck.start(config, android, log, execute)
        except OSError as e:
            print("Port %s is busy - is USB Deck already running? (%s)" % (android.get("port", 2346), e))
            sys.exit(2)

    if not config.data.get("iphone", True):
        while True:          # Android only: the web deck threads do the work
            time.sleep(3600)

    while True:
        try:
            devices = usbmux.list_usb_devices()
        except usbmux.UsbmuxError as e:
            status("[iPhone] " + str(e))
            time.sleep(3)
            continue
        if wanted_serial:
            devices = [d for d in devices if d["serial"] == wanted_serial]
        if not devices:
            status("Waiting for iPhone on the USB cable...")
            time.sleep(1)
            continue
        dev = devices[0]
        config.maybe_reload()
        try:
            sock = usbmux.connect(dev["id"], PHONE_PORT)
        except usbmux.ConnectRefused:
            status("iPhone found on USB - open the USB Deck app on it.")
            time.sleep(1)
            continue
        except usbmux.UsbmuxError as e:
            status("Connect failed: %s" % e)
            time.sleep(2)
            continue
        status("Connected to iPhone over USB (%s)." % dev["serial"][:8])
        Session(sock, config, pool).run()
        status("Disconnected.")
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
