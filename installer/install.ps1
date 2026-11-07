# PocketDeck installer (Android phone + Windows PC). Run "Install PocketDeck.bat" rather than this file.
# Installs to %USERPROFILE%\USBDeck, gets Google's adb tool, the music add-on, a desktop shortcut,
# then starts the deck. Re-running it updates the app but keeps your buttons (config.json).
param([string]$Dest = "$env:USERPROFILE\USBDeck", [switch]$NoLaunch, [switch]$NoShortcut)
$ErrorActionPreference = "Continue"   # native tools write warnings to stderr; check results explicitly
$ProgressPreference = "SilentlyContinue"
function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    $t" -ForegroundColor Yellow }

Write-Host "PocketDeck - setup for Android" -ForegroundColor White

# 1. Python ----------------------------------------------------------------------------------
Step "Checking Python 3"
$python = $null
foreach ($c in @(@("py", "-3"), @("python"))) {
  if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
  $exe = & $c[0] @($c[1..9] | Where-Object { $_ }) -c "import sys; print(sys.executable)" 2>$null
  if ($exe) { $exe = "$exe".Trim() }
  if ($exe -and (Test-Path $exe) -and ($exe -notmatch "WindowsApps")) { $python = $exe; break }
}
if (-not $python -and (Get-Command winget -ErrorAction SilentlyContinue)) {
  Warn "Python not found - installing it with winget (current user only)..."
  winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
  $cand = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
  if ($cand) { $python = $cand.FullName }
}
if (-not $python) {
  Write-Host "    Python 3 is needed. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this installer again." -ForegroundColor Red
  exit 1
}
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
Ok "Python: $python"

# 2. App files -------------------------------------------------------------------------------
Step "Copying USB Deck to $Dest"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$src = Join-Path $PSScriptRoot "app"                                 # release zip layout
if (-not (Test-Path $src)) { $src = Join-Path (Split-Path $PSScriptRoot) "windows" }   # cloned repo layout
$skip = @("config.json", "config.json.bak", ".editor_token", "agent.log", "launcher.log", "icons", "__pycache__", "README.md")
Get-ChildItem $src -Force | Where-Object { $skip -notcontains $_.Name } | ForEach-Object { Copy-Item $_.FullName -Destination $Dest -Recurse -Force }
$starter = if (Test-Path "$src\config.example.json") { "$src\config.example.json" } else { "$src\config.json" }
if (Test-Path "$Dest\config.json") { Ok "Kept your existing buttons (config.json)" }
else { Copy-Item $starter "$Dest\config.json"; Ok "Starter layout installed" }

# 3. Google platform-tools (adb) ---------------------------------------------------------------
Step "Getting Google's adb tool (talks to the phone)"
$adb = "$env:USERPROFILE\platform-tools\adb.exe"
if (Test-Path $adb) { Ok "Already installed" }
else {
  $zip = "$env:TEMP\platform-tools.zip"
  try {
    Invoke-WebRequest "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zip -UseBasicParsing -ErrorAction Stop
    Expand-Archive $zip -DestinationPath $env:USERPROFILE -Force -ErrorAction Stop
  } catch {
    Write-Host "    Couldn't download adb ($_). Get 'SDK Platform-Tools for Windows' from developer.android.com and unzip it into $env:USERPROFILE" -ForegroundColor Red
    exit 1
  }
  Remove-Item $zip
  Ok "Installed to $env:USERPROFILE\platform-tools"
}

# 4. Music add-on (now-playing notch) ----------------------------------------------------------
Step "Installing the music add-on (song info + controls)"
& $python -m pip install --user --disable-pip-version-check --quiet winrt-runtime winrt-Windows.Media.Control `
    winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections 2>&1 | Out-Null
& $python -c "import winrt.windows.media.control" 2>$null
if ($LASTEXITCODE -eq 0) { Ok "Done" } else { Warn "Skipped (pip couldn't install it). The deck still works; the music notch won't show song info." }

# 5. Shortcut --------------------------------------------------------------------------------
if (-not $NoShortcut) {
  Step "Creating the 'USB Deck' desktop shortcut"
  $ws = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "USB Deck.lnk"))
  $lnk.TargetPath = $pythonw; $lnk.Arguments = "`"$Dest\open_editor.pyw`""; $lnk.WorkingDirectory = $Dest
  $lnk.IconLocation = "$Dest\usbdeck.ico"; $lnk.Description = "USB Deck"; $lnk.Save()
  Ok "Done"
}

# 6. Start -----------------------------------------------------------------------------------
if (-not $NoLaunch) {
  Step "Starting USB Deck"
  Start-Process -FilePath $pythonw -ArgumentList "`"$Dest\open_editor.pyw`"" -WorkingDirectory $Dest
  Ok "The laptop app window will open in a few seconds."
}
Write-Host ""
Write-Host "All set. Now follow 'Phone setup' in SETUP-ANDROID.txt." -ForegroundColor White
