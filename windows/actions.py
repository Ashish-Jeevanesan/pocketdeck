"""What a button does on the PC. Windows-only (pure ctypes, no extra packages).

Action types (used in config.json):
  {"type": "open",   "target": "C:\\Work\\Macros"}           folder / file / URL / app / ms-teams:
  {"type": "open",   "target": "notepad.exe", "args": ["x.txt"]}
  {"type": "run",    "command": "powershell -File C:\\x.ps1"}   any command line
  {"type": "hotkey", "keys": "ctrl+shift+m", "focus": "teams", "restore_focus": true}
  {"type": "multi",  "steps": [ {...}, {...} ], "delay_ms": 150}
"""
import os
import subprocess
import threading
import time

IS_WINDOWS = os.name == "nt"


class ActionError(Exception):
    pass


class NeedsChoice(Exception):
    """The app/folder is already open and the tile is set to ask: switch to it or open new?"""


# --------------------------------------------------------------------------- keys
VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "tab": 0x09, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
}
for _i in range(1, 25):
    VK["f%d" % _i] = 0x6F + _i
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}
MODIFIERS = {0x11, 0x10, 0x12, 0x5B}


def parse_keys(spec):
    codes = []
    for part in spec.lower().replace(" ", "").split("+"):
        if part in VK:
            codes.append(VK[part])
        elif len(part) == 1 and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise ActionError("Unknown key '%s' in '%s'" % (part, spec))
    return codes


# --------------------------------------------------------------------------- win32
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    KEYUP, EXTKEY = 0x0002, 0x0001

    def _key(vk, up):
        flags = (KEYUP if up else 0) | (EXTKEY if vk in EXTENDED else 0)
        user32.keybd_event(vk, user32.MapVirtualKeyW(vk, 0) & 0xFF, flags, 0)

    def send_keys(codes):
        try:
            for c in codes:
                _key(c, False)
                time.sleep(0.01)
        finally:
            for c in reversed(codes):
                _key(c, True)
                time.sleep(0.005)

    def _process_name(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(h)

    def _cloaked(hwnd):
        val = ctypes.c_int(0)
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val))
        return val.value != 0

    def list_windows():
        out = []

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd) and not _cloaked(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    out.append((hwnd, buf.value, _process_name(hwnd)))
            return True

        user32.EnumWindows(EnumWindowsProc(cb), 0)
        return out

    def focus_window(hwnd):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        me = kernel32.GetCurrentThreadId()
        attached = []
        for tid in {user32.GetWindowThreadProcessId(fg, None) if fg else 0,
                    user32.GetWindowThreadProcessId(hwnd, None)}:
            if tid and tid != me and user32.AttachThreadInput(me, tid, True):
                attached.append(tid)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            for tid in attached:
                user32.AttachThreadInput(me, tid, False)
        if user32.GetForegroundWindow() != hwnd:
            _key(0x12, False); _key(0x12, True)   # Alt tap unlocks SetForegroundWindow
            user32.SetForegroundWindow(hwnd)
        for _ in range(30):
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.01)
        return False

    def foreground():
        return user32.GetForegroundWindow()

    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.IsZoomed.argtypes = [wintypes.HWND]

    def _app_window(hwnd):
        """A normal resizable app window (skips splash screens, tool windows, the taskbar)."""
        style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF
        exstyle = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
        return bool(style & 0x00010000) and not (exstyle & 0x80) and not user32.GetWindow(hwnd, 4)

    def show_window(hwnd, mode):
        if mode == "maximize":
            user32.ShowWindow(hwnd, 3)                    # SW_MAXIMIZE
        focus_window(hwnd)
        if mode == "maximize":
            time.sleep(0.8)                               # some apps restore their saved size
            if not user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, 3)
                focus_window(hwnd)

    def bring_up(before, expect_proc, mode):
        """After launching something: find its window (new, or the existing one) and show it."""
        known = {h for h, _, _ in before}
        expect_proc = set(expect_proc or ())
        running = bool(expect_proc) and any(p in expect_proc for _, _, p in before)
        t0 = time.time()
        target = None
        while time.time() - t0 < 12:
            fresh = [(h, p) for h, _, p in list_windows() if h not in known and _app_window(h)]
            if expect_proc:
                match = [h for h, p in fresh if p in expect_proc]
                if match:
                    target = match[0]
                    break
                if running and time.time() - t0 > 1.5:
                    break                                 # single-instance app: use its window
                if time.time() - t0 > 3 and fresh:        # Store apps run under another process
                    target = fresh[0][0]
                    break
            elif fresh:
                target = fresh[0][0]
                break
            time.sleep(0.15)
        if not target and expect_proc:
            for h, _, p in list_windows():                # EnumWindows is top-most first
                if p in expect_proc and _app_window(h):
                    target = h
                    break
        if not target:
            return False
        time.sleep(0.25)
        show_window(target, mode)
        return True

else:  # non-Windows: dry run, so the agent can be tested anywhere
    def send_keys(codes):
        print("    [dry-run] keys", [hex(c) for c in codes])

    def list_windows():
        return []

    def focus_window(hwnd):
        return True

    def foreground():
        return None

    def bring_up(before, expect_proc, mode):
        print("    [dry-run] bring to front (%s), process %s" % (mode, expect_proc))
        return True


# --------------------------------------------------------------------------- focus profiles
DEFAULT_PROFILES = {
    "teams": {
        "processes": ["ms-teams.exe", "teams.exe"],
        # main-window sections; the meeting window is preferred over these
        "avoid_titles": ["chat |", "activity |", "calendar |", "teams |", "calls |", "files |",
                         "apps |", "onedrive |", "copilot |", "assignments |", "people |",
                         "microsoft teams"],
        "prefer_titles": ["meeting", "call", "sharing"],
    }
}


def find_window(profile):
    procs = [p.lower() for p in profile.get("processes", [])]
    avoid = [t.lower() for t in profile.get("avoid_titles", [])]
    prefer = [t.lower() for t in profile.get("prefer_titles", [])]
    best, best_score = None, -1
    for hwnd, title, proc in list_windows():
        if procs and proc not in procs:
            continue
        t = title.lower()
        score = 2
        if any(t.startswith(a) or t == a for a in avoid):
            score = 1
        if any(p in t for p in prefer):
            score = 3
        if score > best_score:
            best, best_score = hwnd, score
    return best


# --------------------------------------------------------------------------- window placement
DEFAULT_WINDOW = "maximize"     # set from config.json "open_window"


# Launchers that hand over to a different program (classic -> new Outlook, Store apps...)
PROCESS_ALIASES = {
    "outlook.exe": ["outlook.exe", "olk.exe"],
    "msteams.exe": ["ms-teams.exe"],
    "calc.exe": ["calculatorapp.exe"],
    "notepad.exe": ["notepad.exe"],
}


_SIBLINGS = {}
_GENERIC_DIRS = ("\\windows\\", "\\windowsapps\\")


def _sibling_exes(folder, depth=2, limit=60):
    """Lower-case .exe names under an app's own install folder (cached). Skips system folders."""
    low = folder.lower() + "\\"
    if any(g in low for g in _GENERIC_DIRS) or len(folder) <= 3:
        return []
    if folder not in _SIBLINGS:
        found = []
        base_depth = folder.rstrip("\\/").count(os.sep)
        for root, dirs, files in os.walk(folder):
            if root.count(os.sep) - base_depth >= depth:
                dirs[:] = []
            found += [f.lower() for f in files if f.lower().endswith(".exe")]
            if len(found) > limit:
                break
        _SIBLINGS[folder] = found[:limit]
    return _SIBLINGS[folder]


def _expected_process(target):
    """Which processes may own the window we just asked for (lower-case exe names)."""
    try:
        if os.path.isdir(target):
            return ["explorer.exe"]
        import icons
        src = icons.icon_source({"type": "open", "target": target}, {})
        name = None
        if src and src.lower().endswith(".exe"):
            name = os.path.basename(src).lower()
        elif src and src.lower().startswith("shell:appsfolder\\"):
            name = src.split("\\", 1)[1].split("_", 1)[0].lower() + ".exe"   # Claude_xxx!Claude -> claude.exe
        if name:
            names = list(PROCESS_ALIASES.get(name, [name]))
            # Launcher stubs (sqldeveloper.exe -> bin\\sqldeveloper64W.exe, many Java/Electron apps):
            # count any program installed alongside the one we start.
            if src and os.path.isfile(src) and os.path.isabs(src):
                stem = name[:-4]
                names += [n for n in _sibling_exes(os.path.dirname(src))
                          if len(stem) >= 4 and (n.startswith(stem) or stem.startswith(n[:-4]))]
            return sorted(set(names))
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- runner
def _expand(s):
    return os.path.expandvars(os.path.expanduser(s))


DEFAULT_IF_OPEN = "ask"         # set from config.json "if_open": ask | switch | new


def pwa_name(text):
    """Installed web app (Chrome/Edge 'app') behind a target or command line, e.g. YouTube Music.
    Matches shell:AppsFolder\\Chrome._crx_<id> and chrome_proxy.exe --app-id=<id>."""
    import re
    m = re.search(r"_crx_([a-p]{10,32})", text or "", re.I) or re.search(r"--app-id=([a-p]{32})", text or "", re.I)
    if not m:
        return None
    key = m.group(1).lower()
    try:
        import icons
        icons.start_apps()
        for aid, name in getattr(icons, "START_NAMES", {}).items():
            mm = re.search(r"_crx_([a-p]+)$", aid)
            if not mm:
                continue
            short = mm.group(1)              # Windows shortens the 32-char id to first 10 + last 16
            if key == short or (len(key) == 32 and key.startswith(short[:10]) and key.endswith(short[10:])):
                return name
    except Exception:
        pass
    return None


def find_pwa_window(name):
    """Window of an installed web app: a browser window whose title names the app."""
    low = name.lower()
    for h, t, p in list_windows():
        tl = t.lower()
        if p in ("chrome.exe", "msedge.exe", "brave.exe") and _app_window(h) and (
                tl == low or tl.endswith("| " + low) or tl.endswith("- " + low) or tl.startswith(low + " -")):
            return h
    return None


def find_existing(target):
    """Window already showing this folder/app, or None. Web links and documents are not checked."""
    if not IS_WINDOWS:
        return "fake" if os.environ.get("DECK_FAKE_OPEN") else None
    pwa = pwa_name(target)
    if pwa:
        return find_pwa_window(pwa)
    if os.path.isdir(target):
        name = os.path.basename(target.rstrip("\\/")) or target
        for h, t, p in list_windows():
            if p == "explorer.exe" and _app_window(h) and (
                    t == name or t.startswith(name + " - ") or t.startswith(name + " and ")):
                return h
        return None
    low = target.lower()
    is_app = (low.startswith("shell:appsfolder\\") or low.startswith("msteams:") or low.startswith("ms-teams:")
              or (not os.path.dirname(target) and ":" not in target) or low.endswith(".exe"))
    if not is_app:
        return None
    procs = _expected_process(target)
    if not procs:
        return None
    for h, t, p in list_windows():                      # top-most first
        if p in procs and _app_window(h):
            return h
    return None


def run_action(action, profiles, choice=None):
    """choice: None = follow the tile's 'if_open' (may raise NeedsChoice),
    'switch' / 'new' = the user's answer, 'noask' = like None but never ask (iPhone, tests)."""
    kind = action.get("type")

    if kind == "open":
        target = _expand(action["target"])
        looks_like_path = (len(target) > 2 and target[1] == ":") or target.startswith("\\\\")
        if looks_like_path and not os.path.exists(target):
            raise ActionError("Not found: %s" % target)
        args = action.get("args")
        mode = action.get("window") or DEFAULT_WINDOW          # maximize | front | normal
        policy = choice if choice in ("switch", "new") else (action.get("if_open") or DEFAULT_IF_OPEN)
        if policy != "new" and not args:
            existing = find_existing(target)
            if existing:
                if policy == "ask" and choice != "noask":
                    raise NeedsChoice(target)
                if not IS_WINDOWS:
                    print("    [dry-run] switch to existing window of", target)
                    return
                show_window(existing, "maximize" if mode == "maximize" else "front")
                return
        if not IS_WINDOWS:
            print("    [dry-run] open", target, args or "")
            return
        # ShellExecute can block for seconds while a big app (Outlook) starts.
        # Launch in the background; report failure only if it fails fast.
        err = []
        before = list_windows() if mode != "normal" else []
        expect = _expected_process(target) if mode != "normal" else None

        def launch():
            try:
                if args:
                    subprocess.Popen([target] + [_expand(a) for a in args], cwd=action.get("cwd"))
                else:
                    os.startfile(target)
            except Exception as e:
                err.append(e)
                return
            if mode != "normal":
                try:
                    bring_up(before, expect, mode)
                except Exception:
                    pass

        t = threading.Thread(target=launch, daemon=True)
        t.start()
        t.join(0.4)
        if err:
            raise ActionError("Could not open %s: %s" % (target, err[0]))
        return

    if kind == "run":
        cmd = action["command"]
        cmd_text = cmd if isinstance(cmd, str) else " ".join(cmd)
        pwa = pwa_name(cmd_text)                     # e.g. YouTube Music via chrome_proxy --app-id
        mode = action.get("window") or DEFAULT_WINDOW
        if pwa:
            policy = choice if choice in ("switch", "new") else (action.get("if_open") or DEFAULT_IF_OPEN)
            existing = (find_pwa_window(pwa) if IS_WINDOWS
                        else ("fake" if os.environ.get("DECK_FAKE_OPEN") else None))
            if existing and policy != "new":
                if policy == "ask" and choice != "noask":
                    raise NeedsChoice(pwa)
                if IS_WINDOWS:
                    show_window(existing, "maximize" if mode == "maximize" else "front")
                return
        if not IS_WINDOWS:
            print("    [dry-run] run", cmd)
            return
        before = list_windows() if (pwa and mode != "normal") else None
        flags = 0x08000000 if action.get("hidden", True) else 0   # CREATE_NO_WINDOW
        subprocess.Popen(_expand(cmd) if isinstance(cmd, str) else [_expand(c) for c in cmd],
                         shell=isinstance(cmd, str), cwd=action.get("cwd"), creationflags=flags)
        if before is not None:                       # bring the new app window up like Open tiles do
            threading.Thread(target=bring_up, args=(before, ["chrome.exe", "msedge.exe"], mode),
                             daemon=True).start()
        return

    if kind == "hotkey":
        codes = parse_keys(action["keys"])
        previous = foreground()
        focus = action.get("focus")
        if focus:
            profile = profiles.get(focus) or DEFAULT_PROFILES.get(focus)
            if profile is None:
                raise ActionError("Unknown focus profile '%s'" % focus)
            hwnd = find_window(profile) if IS_WINDOWS else "dry"
            if not hwnd:
                raise ActionError("%s window not found (is it running?)" % focus)
            if not focus_window(hwnd):
                raise ActionError("Could not bring %s to the front" % focus)
            time.sleep(action.get("settle_ms", 60) / 1000.0)
        send_keys(codes)
        if focus and action.get("restore_focus") and previous:
            time.sleep(0.12)
            focus_window(previous)
        return

    if kind == "media":
        import media
        mgr = media.MANAGER or media.manager()
        mgr.command(action.get("command", "toggle"), action.get("value"))
        return

    if kind == "multi":
        delay = action.get("delay_ms", 150) / 1000.0
        for i, step in enumerate(action.get("steps", [])):
            if i:
                time.sleep(delay)
            run_action(step, profiles, "noask" if choice is None else choice)
        return

    raise ActionError("Unknown action type '%s'" % kind)
