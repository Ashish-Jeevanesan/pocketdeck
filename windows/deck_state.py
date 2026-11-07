"""Shared runtime state: pause switch and phone-link status (used by every transport)."""
import os
import secrets
import threading
import time


class DeckState:
    def __init__(self):
        self.lock = threading.Lock()
        self.paused = False            # soft stop: buttons do nothing, link stays up
        self.paused_by = ""
        self.adb_enabled = True        # hard stop (PC only): drop the ADB tunnel, no auto-reconnect
        self.link = "Waiting for phone"
        self.last_seen = 0.0           # last request from the Android/web deck
        self.iphone = ""               # iPhone status line
        self.editor_token = secrets.token_urlsafe(24)
        self.events = []               # recent log lines for the editor
        self.phone_view = {}           # tiles per phone page the phone reported: {"portrait": n, "landscape": n}

    def set_paused(self, value, who):
        with self.lock:
            self.paused = bool(value)
            self.paused_by = who if value else ""

    def snapshot(self):
        with self.lock:
            online = (time.time() - self.last_seen) < 6
            return {"paused": self.paused, "paused_by": self.paused_by,
                    "adb_enabled": self.adb_enabled, "link": self.link,
                    "phone_online": online, "iphone": self.iphone, "phone_view": dict(self.phone_view),
                    "events": self.events[-40:]}

    def add_event(self, line):
        with self.lock:
            self.events.append(line)
            del self.events[:-200]

    def write_token(self, folder):
        """Reuse the saved editor code so open editor windows keep working after a restart."""
        path = os.path.join(folder, ".editor_token")
        try:
            with open(path) as f:
                old = f.read().strip()
            if len(old) >= 24:
                self.editor_token = old
                return
        except OSError:
            pass
        try:
            with open(path, "w") as f:
                f.write(self.editor_token)
        except OSError:
            pass


STATE = DeckState()
