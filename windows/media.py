"""Now playing + media control through Windows' own media controls (SMTC: the same thing
the volume flyout and lock screen use). Works with YouTube Music, Spotify, Chrome/Edge tabs,
the Media Player app, etc.

Needs:  py -m pip install --user winrt-runtime winrt-Windows.Media.Control
        winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections
Without them (or off Windows) it falls back to plain media keys and reports nothing.
Set DECK_FAKE_MEDIA=1 to simulate a playing track (UI testing on any OS).
"""
import asyncio
import hashlib
import os
import struct
import threading
import time
import zlib

import actions

STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
REPEAT = {0: "none", 1: "track", 2: "list"}
# Shortcuts for apps whose SMTC integration lacks shuffle/repeat (Chrome-based players)
KEY_FALLBACK = {
    "youtube music": {"shuffle": "s", "repeat": "r", "title": "YouTube Music"},
}


def _add_user_site():
    """The music add-on is installed per user (pip --user). Some ways of starting Python
    (startup shortcuts, pythonw, an unset APPDATA) leave that folder off sys.path - add it."""
    import site
    import sys
    cands = []
    try:
        cands.append(site.getusersitepackages())
    except Exception:
        pass
    ver = "Python%d%d" % sys.version_info[:2]
    for base in (os.environ.get("APPDATA"), os.path.join(os.path.expanduser("~"), "AppData", "Roaming")):
        if base:
            cands.append(os.path.join(base, "Python", ver, "site-packages"))
    for c in cands:
        if c and os.path.isdir(c) and c not in sys.path:
            site.addsitedir(c)


def _app_label(aumid):
    low = (aumid or "").lower()
    try:
        import icons
        icons.start_apps()
        if low in getattr(icons, "START_NAMES", {}):
            return icons.START_NAMES[low]
    except Exception:
        pass
    for k, v in (("spotify", "Spotify"), ("chrome", "Chrome"), ("msedge", "Edge"), ("firefox", "Firefox"),
                 ("zunemusic", "Media Player"), ("vlc", "VLC")):
        if k in low:
            return v
    return aumid.split("!")[-1].split(".")[0] if aumid else ""


class Media:
    def __init__(self, log=print):
        self.log = log
        self.lock = threading.Lock()
        self.state = {"active": False}
        self.art = (b"", "image/png", "")
        self.fake = bool(os.environ.get("DECK_FAKE_MEDIA"))
        self.available = False
        self.loop = None
        self.session = None
        self._last_props_key = None
        if self.fake:
            self._fake_init()
            self.available = True
            return
        if os.name != "nt":
            return
        _add_user_site()
        try:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager  # noqa
            self.available = True
        except Exception as e:
            self.log("[media] Windows media controls not available (%s) - using media keys only" % e)
            return
        threading.Thread(target=self._run, name="media", daemon=True).start()

    # ------------------------------------------------------------------ public
    def snapshot(self):
        with self.lock:
            s = dict(self.state)
        if s.get("active") and s.get("status") == "playing" and s.get("duration"):
            s["position"] = min(s["duration"], s["position"] + (time.time() - s["sampled"]))
            s["sampled"] = time.time()
        s.pop("sampled", None)
        return s

    def art_bytes(self):
        return self.art

    def command(self, action, value=None):
        """toggle | play | pause | next | prev | stop | seek(seconds) | shuffle | repeat"""
        if self.fake:
            return self._fake_command(action, value)
        if not self.available or not self.loop:
            return self._key_fallback(action)
        fut = asyncio.run_coroutine_threadsafe(self._command(action, value), self.loop)
        try:
            return fut.result(timeout=4)
        except Exception as e:
            raise actions.ActionError("Media control failed: %s" % e)

    # ------------------------------------------------------------------ worker
    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._poll())
        self.loop.run_forever()

    async def _poll(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MM
        mgr = None
        while True:
            try:
                if mgr is None:
                    mgr = await MM.request_async()
                await self._refresh(mgr)
            except Exception as e:
                self.log("[media] %s" % e)
                mgr = None
                await asyncio.sleep(3)
            await asyncio.sleep(0.5)

    async def _refresh(self, mgr):
        sessions = list(mgr.get_sessions())
        cur = mgr.get_current_session()
        playing = [s for s in sessions if s.get_playback_info().playback_status == 4]
        s = playing[0] if playing else (cur or (sessions[0] if sessions else None))
        self.session = s
        if s is None:
            with self.lock:
                self.state = {"active": False}
            return
        info = s.get_playback_info()
        tl = s.get_timeline_properties()
        c = info.controls
        aumid = s.source_app_user_model_id or ""
        props = await s.try_get_media_properties_async()
        key = (aumid, props.title, props.artist, props.album_title)
        if key != self._last_props_key:
            self._last_props_key = key
            await self._load_art(props)
        app = _app_label(aumid)
        fb = next((v for k, v in KEY_FALLBACK.items() if k in app.lower()), None)
        dur = max(0.0, (tl.end_time - tl.start_time).total_seconds())
        pos = max(0.0, (tl.position - tl.start_time).total_seconds())
        try:
            sampled = tl.last_updated_time.timestamp()
        except Exception:
            sampled = time.time()
        status = STATUS.get(int(info.playback_status), "unknown")
        if status != "playing":
            sampled = time.time()
        shuffle = info.is_shuffle_active
        repeat = info.auto_repeat_mode
        with self.lock:
            self.state = {
                "active": bool(props.title) and status in ("playing", "paused", "changing", "stopped"),
                "app": app, "aumid": aumid,
                "title": props.title or "", "artist": props.artist or "", "album": props.album_title or "",
                "status": status, "position": pos, "duration": dur, "sampled": sampled,
                "art": "/api/media/art?v=%s" % self.art[2] if self.art[0] else None,
                "app_icon": "/api/media/appicon?aumid=%s" % aumid if aumid else None,
                "shuffle": None if shuffle is None else bool(shuffle),
                "repeat": None if repeat is None else REPEAT.get(int(repeat), "none"),
                "can": {"prev": bool(c.is_previous_enabled), "next": bool(c.is_next_enabled),
                        "toggle": bool(c.is_play_pause_toggle_enabled or c.is_play_enabled or c.is_pause_enabled),
                        "seek": bool(c.is_playback_position_enabled) and dur > 0,
                        "shuffle": bool(c.is_shuffle_enabled) or bool(fb),
                        "repeat": bool(c.is_repeat_enabled) or bool(fb)},
                "shuffle_via_keys": not c.is_shuffle_enabled and bool(fb),
            }

    async def _load_art(self, props):
        data, ctype = b"", "image/png"
        try:
            if props.thumbnail:
                from winrt.windows.storage.streams import DataReader
                st = await props.thumbnail.open_read_async()
                r = DataReader(st)
                n = await r.load_async(st.size)
                data = bytes(r.read_buffer(n))
                ctype = st.content_type or ("image/jpeg" if data[:2] == b"\xff\xd8" else "image/png")
        except Exception as e:
            self.log("[media] no album art: %s" % e)
        self.art = (data, ctype, hashlib.sha1(data).hexdigest()[:10] if data else "")

    async def _command(self, action, value):
        s = self.session
        if s is None:
            return self._key_fallback(action)
        info = s.get_playback_info()
        if action == "toggle":
            ok = await s.try_toggle_play_pause_async()
        elif action == "play":
            ok = await s.try_play_async()
        elif action == "pause":
            ok = await s.try_pause_async()
        elif action == "stop":
            ok = await s.try_stop_async()
        elif action == "next":
            ok = await s.try_skip_next_async()
        elif action == "prev":
            ok = await s.try_skip_previous_async()
        elif action == "seek":
            tl = s.get_timeline_properties()
            ticks = int((float(value) + tl.start_time.total_seconds()) * 10_000_000)
            ok = await s.try_change_playback_position_async(ticks)
            with self.lock:
                if self.state.get("active"):
                    self.state["position"], self.state["sampled"] = float(value), time.time()
        elif action in ("shuffle", "repeat"):
            if (action == "shuffle" and info.controls.is_shuffle_enabled) or \
               (action == "repeat" and info.controls.is_repeat_enabled):
                if action == "shuffle":
                    ok = await s.try_change_shuffle_active_async(not bool(info.is_shuffle_active))
                else:
                    nxt = {0: 2, 2: 1, 1: 0}[int(info.auto_repeat_mode or 0)]
                    ok = await s.try_change_auto_repeat_mode_async(nxt)
            else:
                return self._shortcut(action)
        else:
            raise actions.ActionError("Unknown media command '%s'" % action)
        if not ok:
            raise actions.ActionError("The player ignored '%s'" % action)
        return True

    def _shortcut(self, action):
        """Shuffle/repeat for players that only support them by keyboard (YouTube Music)."""
        app = (self.state.get("app") or "").lower()
        fb = next((v for k, v in KEY_FALLBACK.items() if k in app), None)
        if not fb:
            raise actions.ActionError("This player doesn't support %s" % action)
        hwnd = None
        for h, t, p in actions.list_windows():
            if fb["title"].lower() in t.lower():
                hwnd = h
                break
        if not hwnd:
            raise actions.ActionError("%s window not found" % fb["title"])
        prev = actions.foreground()
        actions.focus_window(hwnd)
        time.sleep(0.08)
        actions.send_keys(actions.parse_keys(fb[action]))
        time.sleep(0.1)
        if prev and prev != hwnd:
            actions.focus_window(prev)
        return True

    @staticmethod
    def _key_fallback(action):
        keys = {"toggle": "media_play_pause", "play": "media_play_pause", "pause": "media_play_pause",
                "next": "media_next", "prev": "media_prev", "stop": "media_stop"}.get(action)
        if not keys:
            raise actions.ActionError("Nothing is playing")
        actions.send_keys(actions.parse_keys(keys))
        return True

    # ------------------------------------------------------------------ fake (UI testing)
    def _fake_init(self):
        w = h = 64
        px = bytearray()
        for y in range(h):
            for x in range(w):
                px += bytes((200 - y, 60 + x * 2, 140 + (x + y) // 2, 255))
        raw = b"".join(b"\x00" + bytes(px[y * w * 4:(y + 1) * w * 4]) for y in range(h))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        self.art = (png, "image/png", "fake1")
        self.state = {"active": True, "app": "YouTube Music", "aumid": "", "title": "LUNAR ECLIPSE 2 (Super Slowed)",
                      "artist": "Gxlden Anxnymxus and NXRX", "album": "LUNAR ECLIPSE 2", "status": "playing",
                      "position": 13.0, "duration": 144.3, "sampled": time.time(), "art": "/api/media/art?v=fake1",
                      "app_icon": None, "shuffle": None, "repeat": None, "shuffle_via_keys": True,
                      "can": {"prev": True, "next": True, "toggle": True, "seek": True, "shuffle": True, "repeat": True}}

    def _fake_command(self, action, value):
        with self.lock:
            s = self.state
            now = time.time()
            if s["status"] == "playing":
                s["position"] = min(s["duration"], s["position"] + now - s["sampled"])
            s["sampled"] = now
            if action in ("toggle", "play", "pause"):
                s["status"] = "paused" if (action == "pause" or (action == "toggle" and s["status"] == "playing")) else "playing"
            elif action == "seek":
                s["position"] = float(value)
            elif action in ("next", "prev"):
                s["position"] = 0.0
                s["title"] = ("Next" if action == "next" else "Previous") + " song"
        return True


MANAGER = None


def manager(log=print):
    global MANAGER
    if MANAGER is None:
        MANAGER = Media(log)
    return MANAGER
