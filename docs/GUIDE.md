# USB Deck: an iPhone-as-Stream-Deck that only works over the cable

```
 iPhone 6s (USB Deck app)  ──Lightning cable──►  Windows laptop (deck_agent.py)
 listens on 127.0.0.1:2345        usbmuxd          opens folders / apps,
 (unreachable from Wi-Fi)    (Apple Mobile Device   sends Teams hotkeys
                                  Service)
```

* **Cable only.** The phone app listens on loopback only, so no network can reach it. The one way in is Apple's USB tunnel (the same one Xcode and iTunes use). The agent also ignores phones that iTunes sees over Wi-Fi. Pull the cable and the deck goes dim.
* **Low latency.** Buttons fire when your finger touches down. A round trip over USB takes a few milliseconds, and Teams buttons take about 100 ms more to bring the Teams window to the front.
* **Edit on the PC, no reinstall.** `windows/config.json` controls every button. Save the file and the phone updates right away.
* A button flashes **green** if the action worked and **red** if it failed, for example when a folder doesn't exist or Teams isn't running.

---

## One-time setup (about 30 minutes)

### 1. Laptop prerequisites
1. Install **iTunes** and **iCloud** from apple.com: the web installers, *not* the Microsoft Store versions. Sideloadly needs these, and they also install the **Apple Mobile Device Service** that the agent talks to.
2. Install **Python 3** from python.org. The default options include the `py` launcher. The agent only uses Python's standard library, so there's nothing to `pip install`.
3. Plug in the iPhone, unlock it, and tap **Trust This Computer**.

> On a company-managed laptop, IT policy may block iTunes or Python installs. If an install is blocked, check with IT.

### 2. Build the iPhone app in the cloud (free, no Mac needed)
1. Create a GitHub account if you don't have one, then create a new **private** repository, for example `usbdeck`.
2. Upload everything in this folder, including the hidden `.github` folder. **GitHub Desktop** is the easiest way. The web "upload files" page also works.
3. In the repo, open the **Actions** tab, select **Build USB Deck (unsigned IPA)**, and click **Run workflow**. The build takes about 5 minutes.
4. Open the finished run and download the **USBDeck-ipa** artifact. Unzip it to get `USBDeck.ipa`.

If the build fails, copy the red error lines and send them to me.

### 3. Install the app on the iPhone
1. Install **Sideloadly** (sideloadly.io) on Windows.
2. With the phone plugged in, drag `USBDeck.ipa` into Sideloadly, enter your Apple ID, and click **Start**. You can use a spare Apple ID if you'd rather not use your main one.
3. On the iPhone, go to **Settings › General › VPN & Device Management**, tap your Apple ID, and tap **Trust**.

**With a free Apple ID, the app stops opening after 7 days.** To fix it, plug in the phone and run Sideloadly again. It takes about a minute, and Sideloadly has an auto-refresh option that can do it for you. A paid Apple Developer account ($99/yr) extends this to one year.

### 4. Run it
1. Double-click `windows\start_deck.bat`.
2. Open **USB Deck** on the phone. The dot turns green and your buttons appear.
3. Optional: run `windows\autostart_on_login.bat` to start the agent every time you sign in.

---

## Customising buttons (`windows/config.json`)

```json
{ "id": "fld_macros", "label": "Macros", "icon": "folder.fill", "color": "#1E8449",
  "action": { "type": "open", "target": "D:\\Work\\Macros" } }
```

| Field | Meaning |
|---|---|
| `id` | Any unique name |
| `label` | Text on the key |
| `icon` | An Apple **SF Symbols** name, e.g. `folder.fill`, `mic.slash.fill`, `terminal`, `envelope.fill`. Browse them with the free "SF Symbols" viewer apps or on sfsymbols.com |
| `color` | Hex colour |
| `action` | See below |

**Action types**

| Type | Example | Use for |
|---|---|---|
| `open` | `{"type":"open","target":"%USERPROFILE%\\Documents\\Macros"}` | Folders, files, `.xlsm` macros, URLs, apps (`outlook.exe`), protocols (`msteams:`) |
| `open` + args | `{"type":"open","target":"C:\\Tools\\app.exe","args":["--x"]}` | An app with arguments |
| `run` | `{"type":"run","command":"powershell -File C:\\s\\job.ps1"}` | Any command line. Runs hidden; add `"hidden": false` to show its window |
| `hotkey` | `{"type":"hotkey","keys":"ctrl+shift+m","focus":"teams","restore_focus":true}` | Keyboard shortcuts. `focus` brings that app to the front first |
| `multi` | `{"type":"multi","steps":[{...},{...}],"delay_ms":200}` | Several steps in a row |

Key names: `ctrl shift alt win`, `a`–`z`, `0`–`9`, `f1`–`f24`, `enter esc space tab`, arrows, `home end pageup pagedown delete`, `volume_up volume_down volume_mute media_play_pause media_next media_prev`.

In JSON, backslashes in paths must be doubled: `"C:\\Work\\Macros"`.

`columns` sets keys per row in portrait (default 3); `columns_landscape` sets it for landscape. Swipe left and right to switch pages.

### Teams buttons
Teams shortcuts only work when a Teams window is in front. The `teams` focus profile brings the **meeting window** to the front, sends the shortcut, and with `restore_focus` switches back to the window you were in.

| Button | Shortcut |
|---|---|
| Mute / Unmute | Ctrl+Shift+M |
| Camera on/off | Ctrl+Shift+O |
| Share screen (opens the share tray) | Ctrl+Shift+E |
| Raise / lower hand | Ctrl+Shift+K |
| Leave call | Ctrl+Shift+H |

Tip for Windows 11: **Win+Alt+K** mutes Teams from anywhere, without changing focus. To use it, set the mute key to `"keys": "win+alt+k"` and remove `focus`.

---

## Troubleshooting

| Agent says | Fix |
|---|---|
| Cannot reach Apple Mobile Device Service | Install iTunes (step 1), or restart the "Apple Mobile Device Service" in `services.msc` |
| Waiting for iPhone on the USB cable | Unlock the phone, tap Trust, or try another cable or port |
| iPhone found - open the USB Deck app | Open the app. It must be on screen, because iOS pauses apps in the background |
| Button flashes red | The agent window shows the reason, e.g. `Not found: C:\...` or `teams window not found` |

Tips: keep the phone on the cable with auto-lock off. The app already keeps the screen on while it's open. **Guided Access** (Settings › Accessibility) locks the phone into the deck.

## Files
```
ios/                     SwiftUI app (iOS 15+, iPhone & iPad), built by GitHub Actions
  project.yml            XcodeGen project definition
  USBDeck/*.swift        UI + loopback socket server
.github/workflows/       cloud build → unsigned USBDeck.ipa
windows/
  deck_agent.py          main agent (USB connection, layout push, ping/reconnect)
  usbmux.py              tiny client for Apple Mobile Device Service
  actions.py             open / run / hotkey / focus-window (Win32 via ctypes)
  config.json            your buttons
  start_deck.bat, autostart_on_login.bat
```

---

## Android (Samsung S23 etc.): web-app deck over ADB

No app store and no APK. The agent serves a small full-screen web app on **127.0.0.1:2346**. The phone reaches it through `adb reverse`, which runs over the **USB cable or Wireless debugging**. Nothing listens on your Wi-Fi/LAN, so no firewall change is needed and nobody else on the network can press your buttons.

**One-time setup**
1. Install Google **platform-tools** (adb) into `%USERPROFILE%\platform-tools`, or set `android.adb_path` in `config.json`.
2. On the phone, turn on **Developer options → USB debugging** and/or **Wireless debugging**. Pair it once:
   - Wireless: tap *Pair device with pairing code*, then run `adb pair <ip:port> <code>` on the laptop.
   - USB: tap *Allow* on the phone.
3. Run the agent. It finds the phone by itself (USB first, otherwise Wi-Fi via mDNS) and sets up `adb reverse`.
4. On the phone, open Chrome at **http://127.0.0.1:2346**, then **⋮ → Add to Home screen → Install**.

**Daily use:** Wireless debugging switches itself off after a reboot or when the phone changes Wi-Fi. Turn it back on (add the *Wireless debugging* quick-settings tile from Developer options to make this one tap), and the agent reconnects within a few seconds. The page shows a green dot when it's linked.

Settings in `config.json`:
```json
"iphone": true,
"android": { "enabled": true, "port": 2346, "adb": true, "adb_path": "%USERPROFILE%\\platform-tools\\adb.exe" }
```
Buttons can set `"emoji": "🚀"`. Otherwise an emoji is picked from the SF Symbol name.

---

## Laptop app: tile editor + connection controls

Double-click **USB Deck** on the desktop. It starts the agent in the background if needed and opens the editor in its own window.

* **Drag tiles** to reorder them. Drop a tile on a page tab to move it to that page.
* **Click a tile** to edit its label, emoji, colour and action:
  * **Open:** the 📁/📄 buttons open a Windows picker.
  * **Keyboard shortcut:** click the box and press the keys, or use *Quick pick* (Teams shortcuts are built in).
  * **Run command**, or **Advanced JSON** for multi-step actions.
  * **▶ Test** runs the action on the PC straight away.
* **+ Page / + Add tile**, rename or reorder pages, and set tiles per row.
* Changes **save automatically**, and the phone redraws within about 2 seconds. **Undo** (Ctrl+Z) steps back. The previous file is kept as `config.json.bak`.
* The bottom strip shows a live log of every press, with errors in red.

**Connection controls**

| Control | Where | What it does |
|---|---|---|
| ⏸ Pause / ▶ Resume | Laptop app **and** phone (⏻ button) | Buttons stop working while the link stays up. Resume from either side. |
| ⏏ Disconnect / 🔌 Reconnect | Laptop app | Removes the ADB tunnel, drops the Wi-Fi debugging link and stops auto-reconnect. The phone shows "Not connected". Reconnect from the laptop. |

**Who can reach it:** only this PC (127.0.0.1) and phones you have paired for USB/Wireless debugging. Other devices on the Wi-Fi can't open the deck. The editor also needs a secret token that only the desktop shortcut knows, so the phone can't change your layout.

---

## Tile pictures: real app icons or your own images

In the editor, click a tile and choose **Picture**:

| Option | What shows on the phone |
|---|---|
| 😀 **Emoji** | The emoji. It also works in the iPhone app. |
| 🖼 **App icon** | The real Windows icon of whatever the tile opens: Outlook, Excel, Teams, Edge (for web links), Notepad, a folder, the program behind a `.xlsx`, and so on. It's pulled from Windows at 256 px and cached in `icons\`. |
| ⬆ **My image** | Any PNG/JPG you choose. It's resized to 256 px in the browser and stored in `icons\`. |

Page settings have **Use app icons on this page** and **Use emoji on this page**. Plain keyboard shortcuts (volume, Teams mute) have no app of their own, so they keep their emoji. In config.json this is the `"image"` field: `"app"` or `"file:<name>.png"`.

---

## Opening apps maximised and in front

Windows normally won't let a background program (the agent) push a new window to the front, so apps used to open behind your current window. Now, after an **Open** tile launches something, the agent finds that window and brings it forward. That can be a new window, or the existing one for single-instance apps like Outlook, Teams and Claude.

* Deck-wide default, in the editor's page panel: **Maximise + bring to front** (default), **Bring to front (keep size)**, or **Leave as Windows opens it**. In config.json this is `"open_window": "maximize" | "front" | "normal"`.
* Per tile, using the **Window** setting under the action: `"window": ...` overrides the default.
* Launchers that hand over to another program are handled, for example `outlook.exe` → new Outlook (`olk.exe`). Add more in `PROCESS_ALIASES` in actions.py if needed.

---

## When the app or folder is already open

Before an **Open** tile launches anything, the agent checks whether that app or folder already has a window:

| Setting | Behaviour |
|---|---|
| **Ask** (default) | The phone shows a sheet: **Switch to <app>** / **Open New Window** / Cancel |
| **Switch** | Brings the existing window to the front (maximised if that's your window setting) |
| **New** | Always opens another window |

* Deck-wide: page panel in the editor, **If that app or folder is already open**. In config.json: `"if_open": "ask" | "switch" | "new"`.
* Per tile: the **If it's already open** setting under the action (`"if_open"`).
* It's checked for folders (by the Explorer window title) and apps. Web links and documents always just open.
* Single-instance apps (new Outlook, Teams, Claude) may only switch, even when you pick "Open New Window".
* The iPhone app never asks: an "Ask" tile behaves like Switch there.

---

## Now playing: Dynamic-Island-style notch + media control

The agent reads **Windows' media controls** (the same source as the volume flyout and lock screen). It works with YouTube Music (Chrome app), Spotify, browser tabs, Media Player, and so on.

**One-time install on the laptop** (already done on this PC):
```
py -m pip install --user winrt-runtime winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections
```

**On the phone:**
* While music plays, a black **pill appears at the top** with the album art and live equalizer bars tinted from the cover. It lingers for 90 s after you pause, then hides.
* **Tap it** and it springs open into a player: art, title, artist, the app's icon, a **scrubber** you can drag to any timestamp (it thickens under your finger), and **shuffle / previous / play-pause / next / repeat**.
* Tap outside it or swipe up to close it.
* Motion uses real spring physics (Apple's response/damping model), so it can be interrupted mid-animation. It honours *Remove animations*.

**Tiles:** action type **Music / media control** (`{"type":"media","command":"toggle|next|prev|stop|shuffle|repeat"}`). Plain media keys don't reach some players (YouTube Music in Chrome); this does.

**Shuffle / repeat:** Chrome-based players (YouTube Music) don't expose these to Windows, so the agent uses YouTube Music's own `S` / `R` shortcuts. It briefly focuses the YouTube Music window, and the current shuffle/repeat state can't be shown.
