"""Tile pictures: real Windows app/file/folder icons, or images you upload.

A button's "image" field in config.json:
    absent / "emoji"   -> show the emoji (default)
    "app"              -> the icon of whatever the button opens / focuses (Outlook, Teams,
                          a folder, an .xlsx ...) pulled from Windows at 256 px
    "file:<name>.png"  -> an image uploaded from the editor (stored in icons/)

Icons are extracted in a background thread with the Windows Shell API (pure ctypes,
no PowerShell, no extra packages) and cached as PNG files in the icons/ folder.
"""
import hashlib
import os
import queue
import re
import shutil
import struct
import subprocess
import threading
import zlib

IS_WINDOWS = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, "icons")
SIZE = 256


def _expand(s):
    return os.path.expandvars(os.path.expanduser(s or ""))


# --------------------------------------------------------------------------- PNG writer
def write_png(path, w, h, rgba):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- Windows shell
if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD),
                    ("d4", ctypes.c_ubyte * 8)]

        def __init__(self, s):
            super().__init__()
            h = s.replace("-", "")
            self.d1, self.d2, self.d3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
            for i in range(8):
                self.d4[i] = int(h[16 + i * 2:18 + i * 2], 16)

    class SIZE_T(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                    ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD),
                    ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    gdi32 = ctypes.WinDLL("gdi32")
    user32w = ctypes.WinDLL("user32")
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    user32w.GetDC.restype = wintypes.HDC
    user32w.GetDC.argtypes = [wintypes.HWND]
    user32w.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    IID_IMAGE_FACTORY = GUID("bcc18b79-ba16-442f-80c4-8a59c30c463b")
    GetImageProto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE_T, ctypes.c_int,
                                       ctypes.POINTER(wintypes.HBITMAP))
    ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)

    def extract_icon(parsing_name, out_png, size=SIZE):
        """Ask Explorer for the icon of a file/folder/exe/shell:AppsFolder item."""
        obj = ctypes.c_void_p()
        hr = shell32.SHCreateItemFromParsingName(parsing_name, None, ctypes.byref(IID_IMAGE_FACTORY),
                                                 ctypes.byref(obj))
        if hr != 0 or not obj.value:
            raise OSError("not found by Windows Shell (0x%08X)" % (hr & 0xFFFFFFFF))
        vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0],
                           ctypes.POINTER(ctypes.c_void_p))
        hbm = wintypes.HBITMAP()
        try:
            # SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK
            hr = GetImageProto(vtbl[3])(obj, SIZE_T(size, size), 0x4 | 0x1, ctypes.byref(hbm))
        finally:
            ReleaseProto(vtbl[2])(obj)
        if hr != 0 or not hbm.value:
            raise OSError("no icon (0x%08X)" % (hr & 0xFFFFFFFF))
        try:
            bm = BITMAP()
            gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm))
            w, h = bm.bmWidth, abs(bm.bmHeight)
            bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            hdc = user32w.GetDC(None)
            try:
                gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bih), 0)
            finally:
                user32w.ReleaseDC(None, hdc)
        finally:
            gdi32.DeleteObject(hbm)
        px = bytearray(buf.raw)                      # BGRA, premultiplied alpha
        has_alpha = any(px[i] for i in range(3, len(px), 4))
        for i in range(0, len(px), 4):
            b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]
            if not has_alpha:
                a = 255
            elif 0 < a < 255:
                r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
            px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a
        write_png(out_png, w, h, bytes(px))

    def _reg(root, key, name=""):
        try:
            with winreg.OpenKey(root, key) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    def _exe_from_command(cmd):
        if not cmd:
            return None
        cmd = _expand(cmd.strip())
        m = re.match(r'^"([^"]+)"', cmd) or re.match(r"^(\S+\.exe)", cmd, re.I)
        return m.group(1) if m and os.path.isfile(m.group(1)) else None

    def _app_path(exe):
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            v = _reg(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe)
            if v and os.path.isfile(_expand(v.strip('"'))):
                return _expand(v.strip('"'))
        return shutil.which(exe)

    _start_apps = None
    START_NAMES = {}          # AppID (lower) -> display name as Windows shows it

    def start_apps():
        """name -> AppUserModelID for Start-menu apps (incl. Store apps like new Teams)."""
        global _start_apps
        if _start_apps is None:
            _start_apps = {}
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                    capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout
                for line in out.splitlines():
                    if "|" in line:
                        n, a = line.split("|", 1)
                        _start_apps[n.strip().lower()] = a.strip()
                        START_NAMES[a.strip().lower()] = n.strip()
            except Exception:
                pass
        return _start_apps

    def _store_app(*names):
        apps = start_apps()
        for n in names:
            for name, aid in apps.items():
                if name == n or name.startswith(n):
                    return "shell:AppsFolder\\" + aid
        return None

    def _protocol_source(scheme):
        scheme = scheme.lower()
        if scheme in ("http", "https"):
            prog = _reg(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
                        "ProgId")
            exe = _exe_from_command(_reg(winreg.HKEY_CLASSES_ROOT, r"%s\shell\open\command" % prog)) if prog else None
            return exe or _app_path("msedge.exe")
        if scheme in ("msteams", "ms-teams"):
            return _store_app("microsoft teams") or _exe_from_command(
                _reg(winreg.HKEY_CLASSES_ROOT, r"%s\shell\open\command" % scheme))
        return _exe_from_command(_reg(winreg.HKEY_CLASSES_ROOT, r"%s\shell\open\command" % scheme))

else:
    def extract_icon(parsing_name, out_png, size=SIZE):
        raise OSError("icon extraction needs Windows")

    def _app_path(exe):
        return shutil.which(exe)

    def _protocol_source(scheme):
        return None

    def _store_app(*names):
        return None


# --------------------------------------------------------------------------- resolve
def icon_source(action, profiles):
    """Work out which file/app's icon represents this action. None = no sensible icon."""
    if not isinstance(action, dict):
        return None
    t = action.get("type")
    if t == "multi":
        for step in action.get("steps", []):
            src = icon_source(step, profiles)
            if src:
                return src
        return None
    if t == "open":
        target = _expand(action.get("target", "")).strip()
        if not target or target.startswith("\\\\"):          # skip network shares (can hang)
            return None
        if target.lower().startswith("shell:appsfolder\\"):  # Store/MSIX apps (Claude, Teams...)
            return target
        if os.path.exists(target):
            return os.path.abspath(target)
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]+):", target)
        if m and len(m.group(1)) > 1:                         # URL / protocol, not C:\
            return _protocol_source(m.group(1))
        if not os.path.dirname(target):
            exe = target if target.lower().endswith(".exe") else target + ".exe"
            return _app_path(exe)
        return None
    if t == "hotkey" and action.get("focus"):
        focus = action["focus"]
        if focus == "teams":
            return _store_app("microsoft teams") or _protocol_source("msteams")
        for p in (profiles.get(focus) or {}).get("processes", []):
            src = _app_path(p)
            if src:
                return src
    if t == "run":
        cmd = action.get("command")
        first = cmd[0] if isinstance(cmd, list) and cmd else (str(cmd or "").split() or [""])[0]
        first = _expand(first.strip('"'))
        if os.path.isfile(first):
            return first
        if first.lower().endswith(".exe") and first.lower() not in ("rundll32.exe", "cmd.exe"):
            return _app_path(first)
    return None


# --------------------------------------------------------------------------- manager
class IconManager:
    def __init__(self, log=print):
        self.log = log
        self.generation = 0          # bumps when new icons appear -> phone reloads layout
        self.failed = {}             # source -> error text
        self.pending = set()
        self.q = queue.Queue()
        self.src_cache = {}          # json(action) -> source
        os.makedirs(ICON_DIR, exist_ok=True)
        threading.Thread(target=self._worker, name="icons", daemon=True).start()

    @staticmethod
    def _file_for(src):
        return "app_%s.png" % hashlib.sha1(src.lower().encode("utf-8")).hexdigest()[:16]

    def url_for(self, button, profiles):
        """Image URL for a button, or None (phone shows the emoji)."""
        img = button.get("image")
        if not img or img == "emoji":
            return None
        if img.startswith("file:"):
            name = os.path.basename(img[5:])
            p = os.path.join(ICON_DIR, name)
            return "/icons/%s?v=%d" % (name, int(os.path.getmtime(p))) if os.path.isfile(p) else None
        if img != "app":
            return None
        import json
        key = json.dumps(button.get("action"), sort_keys=True)
        if key not in self.src_cache:
            try:
                self.src_cache[key] = icon_source(button.get("action"), profiles)
            except Exception:
                self.src_cache[key] = None
        src = self.src_cache[key]
        if not src:
            return None
        name = self._file_for(src)
        if os.path.isfile(os.path.join(ICON_DIR, name)):
            return "/icons/%s" % name
        if src not in self.pending and src not in self.failed:
            self.pending.add(src)
            self.q.put(src)
        return None

    def _worker(self):
        if IS_WINDOWS:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)       # apartment-threaded COM
        while True:
            src = self.q.get()
            out = os.path.join(ICON_DIR, self._file_for(src))
            try:
                extract_icon(src, out)
                self.generation += 1
            except Exception as e:
                self.failed[src] = str(e)
                self.log("[icons] no icon for %s: %s" % (src, e))
            finally:
                self.pending.discard(src)

    def ensure(self, src):
        """Path of the cached icon for src, queueing extraction if needed (None until ready)."""
        name = self._file_for(src)
        p = os.path.join(ICON_DIR, name)
        if os.path.isfile(p):
            return p
        if src not in self.pending and src not in self.failed:
            self.pending.add(src)
            self.q.put(src)
        return None

    def save_upload(self, button_id, data):
        """Store an uploaded PNG for a tile. Returns the "file:..." value for config."""
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > 2 * 1024 * 1024:
            raise ValueError("Upload must be a PNG under 2 MB")
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", button_id or "tile")[:40]
        name = "custom_%s_%s.png" % (safe, hashlib.sha1(data).hexdigest()[:8])
        with open(os.path.join(ICON_DIR, name), "wb") as f:
            f.write(data)
        return "file:" + name


MANAGER = None


def manager(log=print):
    global MANAGER
    if MANAGER is None:
        MANAGER = IconManager(log)
    return MANAGER
