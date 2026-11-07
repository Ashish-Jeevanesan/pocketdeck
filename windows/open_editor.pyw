"""USB Deck desktop launcher.

Starts the agent in the background if it isn't running, then opens the editor in its
own app window (Microsoft Edge "app mode", no tabs or address bar).

    pythonw open_editor.pyw            -> start agent if needed + open editor
    pythonw open_editor.pyw --agent    -> only make sure the agent is running (for autostart)
"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 2346
URL = "http://127.0.0.1:%d" % PORT


def agent_up():
    try:
        with urllib.request.urlopen(URL + "/api/ping", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def start_agent():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            exe = cand
    log = open(os.path.join(HERE, "agent.log"), "a", encoding="utf-8")
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED | NEW_GROUP | NO_WINDOW
    subprocess.Popen([exe, "-u", os.path.join(HERE, "deck_agent.py")], cwd=HERE,
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     creationflags=flags if os.name == "nt" else 0, close_fds=True)


def ensure_agent():
    if agent_up():
        return True
    start_agent()
    for _ in range(40):
        time.sleep(0.25)
        if agent_up():
            return True
    return False


def open_editor():
    token = ""
    for _ in range(20):
        try:
            with open(os.path.join(HERE, ".editor_token")) as f:
                token = f.read().strip()
            break
        except OSError:
            time.sleep(0.25)
    url = "%s/editor?t=%s" % (URL, token)
    edges = [os.path.expandvars(p) for p in (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe")]
    for e in edges:
        if os.path.exists(e):
            subprocess.Popen([e, "--app=" + url, "--window-size=1280,860"])
            return
    webbrowser.open(url)


def trace(msg):
    try:
        with open(os.path.join(HERE, "launcher.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


if __name__ == "__main__":
    try:
        ok = ensure_agent()
        trace("agent up: %s" % ok)
    except Exception as e:
        trace("ensure_agent failed: %r" % e)
        ok = False
    if "--agent" not in sys.argv:
        if ok:
            try:
                open_editor()
                trace("editor opened")
            except Exception as e:
                trace("open_editor failed: %r" % e)
        else:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, "The USB Deck agent didn't start.\nSee agent.log in %s" % HERE, "USB Deck", 0x10)
            except Exception:
                print("Agent did not start")
