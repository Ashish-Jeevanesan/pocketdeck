"""Android (or any phone) deck + laptop editor: a small web server on 127.0.0.1 only.

The phone reaches it through ADB port-reversing ("adb reverse"), over the USB cable OR
Wireless debugging. Nothing listens on the Wi-Fi/LAN interface, so other devices on the
network cannot open the deck, and no firewall change is needed.

    phone Chrome -> http://127.0.0.1:2346  ==adb tunnel==>  this PC 127.0.0.1:2346
    laptop editor -> http://127.0.0.1:2346/editor?t=<token>  (token only on this PC)
"""
import hmac
import json
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import base64

import actions
import icons
import media
from deck_state import STATE

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".webmanifest": "application/manifest+json", ".png": "image/png"}
STATIC = {"/": "deck.html", "/sw.js": "sw.js", "/manifest.webmanifest": "manifest.webmanifest",
          "/icon-192.png": "icon-192.png", "/icon-512.png": "icon-512.png",
          "/editor": "editor.html"}


def start(config, settings, log, execute):
    port = int(settings.get("port", 2346))
    keeper = AdbKeeper(settings, port, log)

    def status_payload():
        s = STATE.snapshot()
        s.update(ok=True, version=config.version())
        if media.MANAGER:
            s["media"] = media.MANAGER.snapshot()
        return s

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _host_ok(self):
            # Blocks DNS-rebinding tricks from web pages opened on the PC itself.
            host = (self.headers.get("Host") or "").lower()
            return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

        def _editor_ok(self):
            tok = self.headers.get("X-Editor-Token") or ""
            return hmac.compare_digest(tok, STATE.editor_token)

        def _send(self, code, body, ctype="application/json", cache=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        # ----------------------------------------------------------------- GET
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            path = self.path.split("?")[0]
            if path == "/api/config":
                config.maybe_reload()
                STATE.last_seen = time.time()
                return self._send(200, config.phone_layout())
            if path == "/api/ping":
                config.maybe_reload()
                STATE.last_seen = time.time()
                m = re.search(r"[?&]vp=(portrait|landscape):(\d{1,3})", self.path)
                if m:                                   # phone tells us how many tiles fit per page
                    STATE.phone_view[m.group(1)] = int(m.group(2))
                return self._send(200, status_payload())
            if path == "/api/editor/state":
                if not self._editor_ok():
                    return self._send(403, {"error": "editor token required"})
                config.maybe_reload()
                return self._send(200, {"config": config.data, "mtime": config.mtime,
                                        "layout": config.phone_layout(),
                                        "focus_profiles": sorted(set(list(config.profiles)) | {"teams"}),
                                        "status": status_payload()})
            if path == "/api/editor/status":
                if not self._editor_ok():
                    return self._send(403, {"error": "editor token required"})
                config.maybe_reload()
                return self._send(200, status_payload())
            if path == "/api/media":
                return self._send(200, media.MANAGER.snapshot() if media.MANAGER else {"active": False})
            if path == "/api/media/art":
                data, ctype, _ = media.MANAGER.art_bytes() if media.MANAGER else (b"", "", "")
                return self._send(200, data, ctype, cache=True) if data else self._send(404, {"error": "no art"})
            if path == "/api/media/appicon":
                from urllib.parse import parse_qs, urlparse
                aumid = (parse_qs(urlparse(self.path).query).get("aumid") or [""])[0]
                fp = icons.MANAGER.ensure("shell:AppsFolder\\" + aumid) if (aumid and icons.MANAGER) else None
                if fp:
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "image/png", cache=True)
                return self._send(404, {"error": "no icon yet"})
            if path.startswith("/icons/"):
                fn = os.path.basename(path[7:])
                fp = os.path.join(icons.ICON_DIR, fn)
                if fn.endswith(".png") and os.path.isfile(fp):
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "image/png", cache=True)
                return self._send(404, {"error": "no icon"})
            name = STATIC.get(path)
            if name:
                with open(os.path.join(WEB, name), "rb") as f:
                    data = f.read()
                return self._send(200, data, TYPES[os.path.splitext(name)[1]],
                                  cache=name.endswith(".png"))
            self._send(404, {"error": "not found"})

        # ----------------------------------------------------------------- POST
        def do_POST(self):
            n = min(int(self.headers.get("Content-Length") or 0), 2 * 1024 * 1024)
            raw = self.rfile.read(n)       # always drain the body (keep-alive safety)
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            path = self.path.split("?")[0]

            if path.startswith("/api/editor/"):
                if not self._editor_ok():
                    return self._send(403, {"error": "editor token required"})
                return self.editor_post(path, body)

            # Phone endpoints. Custom header forces a CORS preflight, so other
            # websites can't post here.
            if self.headers.get("X-Deck") != "1":
                return self._send(403, {"error": "forbidden"})
            STATE.last_seen = time.time()
            if path == "/api/press":
                choice = body.get("choice") if body.get("choice") in ("switch", "new") else None
                return self._send(200, execute(config, body.get("id"), "android", choice))
            if path == "/api/media":
                act = body.get("action")
                try:
                    if STATE.paused:
                        raise actions.ActionError("Deck is paused - tap Resume")
                    media.MANAGER.command(act, body.get("value"))
                    log("  [android] media %-8s OK%s" % (act, "  %s" % body.get("value") if act == "seek" else ""))
                    return self._send(200, {"ok": True, "media": media.MANAGER.snapshot()})
                except Exception as e:
                    log("  [android] media %-8s ERR %s" % (act, e))
                    return self._send(200, {"ok": False, "message": str(e)})
            if path == "/api/control":
                act = body.get("action")
                if act in ("pause", "resume"):
                    STATE.set_paused(act == "pause", "phone")
                    log("Deck %s from the phone." % ("paused" if act == "pause" else "resumed"))
                    return self._send(200, status_payload())
                return self._send(400, {"error": "unknown action"})
            self._send(404, {"error": "not found"})

        def editor_post(self, path, body):
            if path == "/api/editor/save":
                config.maybe_reload()
                base = body.get("base")
                if base is not None and abs(float(base) - config.mtime) > 1e-6:
                    # someone else (another editor window, Notepad, a script) changed it first
                    return self._send(409, {"ok": False, "error": "Layout changed elsewhere - reloaded",
                                            "config": config.data, "mtime": config.mtime})
                try:
                    config.save(body.get("config") or {})
                except Exception as e:
                    return self._send(400, {"ok": False, "error": str(e)})
                return self._send(200, {"ok": True, "version": config.mtime})
            if path == "/api/editor/test":
                btn = body.get("button") or {}
                try:
                    actions.run_action(btn.get("action") or {}, config.profiles, "noask")
                    log("  [editor] test %-10s OK" % btn.get("id", "?"))
                    return self._send(200, {"ok": True})
                except Exception as e:
                    log("  [editor] test %-10s ERR %s" % (btn.get("id", "?"), e))
                    return self._send(200, {"ok": False, "message": str(e)})
            if path == "/api/editor/upload":
                try:
                    data = base64.b64decode(body.get("png", "").split(",")[-1])
                    value = icons.MANAGER.save_upload(body.get("id"), data)
                    return self._send(200, {"ok": True, "image": value})
                except Exception as e:
                    return self._send(400, {"ok": False, "error": str(e)})
            if path == "/api/editor/layout":
                return self._send(200, config.phone_layout())
            if path == "/api/editor/pick":
                return self._send(200, {"path": pick_path(body.get("kind", "folder"))})
            if path == "/api/editor/control":
                act = body.get("action")
                if act in ("pause", "resume"):
                    STATE.set_paused(act == "pause", "laptop")
                    log("Deck %s from the laptop." % ("paused" if act == "pause" else "resumed"))
                elif act == "disconnect":
                    keeper.disconnect()
                elif act == "reconnect":
                    keeper.reconnect()
                elif act == "connect":
                    ok, msg = keeper.connect_address(str(body.get("address", "")))
                    st = status_payload(); st.update(connect_ok=ok, connect_msg=msg)
                    return self._send(200, st)
                else:
                    return self._send(400, {"error": "unknown action"})
                return self._send(200, status_payload())
            self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.handle_error = lambda request, client_address: None   # phone vanished mid-request
    threading.Thread(target=server.serve_forever, name="web-deck", daemon=True).start()
    log("[android] Deck page on http://127.0.0.1:%d (reachable only through ADB)" % port)
    keeper.start()
    return server


def pick_path(kind):
    """Native Windows folder/file picker for the editor's Browse button."""
    import sys
    code = ("import tkinter as t, tkinter.filedialog as f; r=t.Tk(); r.withdraw(); "
            "r.attributes('-topmost', True); "
            "print(f.askdirectory(title='Choose a folder') if %r=='folder' else "
            "f.askopenfilename(title='Choose a file or app'))" % kind)
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        exe = exe[:-5] + ".exe" if os.path.exists(exe[:-5] + ".exe") else exe
    try:
        r = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=300,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (r.stdout or "").strip().replace("/", "\\") if os.name == "nt" else (r.stdout or "").strip()
    except Exception:
        return ""


# --------------------------------------------------------------------------- ADB
def find_adb(explicit=None):
    cands = [os.path.expandvars(explicit)] if explicit else []
    cands += [os.path.join(os.path.expanduser("~"), "platform-tools", "adb.exe"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk",
                           "platform-tools", "adb.exe"),
              shutil.which("adb")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


class AdbKeeper:
    """Keeps the phone connected (USB or Wireless debugging) with the port reversed."""

    def __init__(self, settings, port, log):
        self.port = port
        self.log = log
        self.adb = find_adb(settings.get("adb_path")) if settings.get("adb", True) else None
        self.serial_of = {}
        self.last_msg = None
        self.wake = threading.Event()

    def start(self):
        if not self.adb:
            self.set_link("adb.exe not found - set android.adb_path in config.json")
            return
        threading.Thread(target=self.loop, name="adb-keeper", daemon=True).start()

    def run(self, *args, timeout=8):
        flags = 0x08000000 if os.name == "nt" else 0          # no console window flash
        try:
            r = subprocess.run([self.adb] + list(args), capture_output=True, text=True,
                               timeout=timeout, creationflags=flags)
            return (r.stdout or "") + (r.stderr or "")
        except (subprocess.TimeoutExpired, OSError) as e:
            return "error: %s" % e

    def set_link(self, m):
        STATE.link = m
        if m != self.last_msg:
            self.log("[android] " + m)
            self.last_msg = m

    def ready(self):
        out = self.run("devices")
        return [l.split()[0] for l in out.splitlines()[1:] if re.search(r"\sdevice$", l)], out

    def disconnect(self):
        """Hard stop: drop the tunnel and Wi-Fi link, and stop auto-reconnecting."""
        STATE.adb_enabled = False
        if self.adb:
            devs, _ = self.ready()
            for t in devs:
                self.run("-s", t, "reverse", "--remove", "tcp:%d" % self.port)
                if ":" in t or "._tcp" in t:
                    self.run("disconnect", t)
        self.set_link("Disconnected from the laptop (press Reconnect)")

    def connect_address(self, addr):
        """Wireless debugging on networks that block discovery (office Wi-Fi): IP:port typed in."""
        addr = addr.strip()
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}:\d{2,5}", addr):
            return False, "Type it as shown on the phone, e.g. 10.80.230.89:34839"
        if not self.adb:
            return False, "adb.exe not found"
        STATE.adb_enabled = True
        out = self.run("connect", addr, timeout=10).strip()
        ok = out.startswith("connected") or "already connected" in out
        if ok:
            self.log("[android] Connected to %s by address" % addr)
            self.wake.set()
        return ok, out

    def reconnect(self):
        STATE.adb_enabled = True
        self.set_link("Reconnecting...")
        self.wake.set()

    def loop(self):
        while True:
            try:
                if STATE.adb_enabled:
                    self.tick()
            except Exception as e:  # never let the keeper die
                self.set_link("adb keeper error: %s" % e)
            self.wake.wait(3)
            self.wake.clear()

    def tick(self):
        ready, out = self.ready()
        if not ready:
            # Wireless debugging: find the phone's current port via mDNS and connect.
            for m in re.finditer(r"_adb-tls-connect\._tcp\s+(\d+\.\d+\.\d+\.\d+:\d+)",
                                 self.run("mdns", "services")):
                self.run("connect", m.group(1))
            ready, out = self.ready()
        if not ready:
            if "unauthorized" in out:
                self.set_link("Phone found but not authorised - tap 'Allow' on the phone")
            else:
                self.set_link("Waiting for phone (USB cable or Wireless debugging)")
            return
        # One transport per physical phone; prefer USB over Wi-Fi.
        chosen = {}
        for t in sorted(ready, key=lambda s: (":" in s or "._tcp" in s)):
            hw = self.serial_of.get(t) or self.run("-s", t, "shell", "getprop",
                                                   "ro.serialno").strip()
            self.serial_of[t] = hw
            chosen.setdefault(hw, t)
        for hw, t in chosen.items():
            if ("tcp:%d" % self.port) not in self.run("-s", t, "reverse", "--list"):
                res = self.run("-s", t, "reverse", "tcp:%d" % self.port, "tcp:%d" % self.port)
                if "error" in res.lower():
                    self.set_link("adb reverse failed on %s: %s" % (t, res.strip()))
                    continue
            kind = "Wi-Fi" if (":" in t or "._tcp" in t) else "USB cable"
            self.set_link("Phone %s linked over %s" % (hw, kind))
