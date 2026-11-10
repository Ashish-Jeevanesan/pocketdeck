<#
.SYNOPSIS
  Builds a PocketDeck release: dist\PocketDeck-v<version>-windows.zip, its SHA-256,
  and draft release notes listing the commits since the previous tag.

.DESCRIPTION
  The zip is made with `git archive` from the committed HEAD, so only tracked files go in
  (never config.json, .editor_token, logs or the icon cache). It then unzips the result into
  a temp folder and runs the installer from it as a smoke test.

.EXAMPLE
  .\tools\build-release.ps1 -Version 1.0.1
  .\tools\build-release.ps1 -Version 1.1.0 -Tag          # also creates the local git tag v1.1.0
  .\tools\build-release.ps1 -Version 1.1.0-beta -AllowDirty -SkipTest
#>
param(
  [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$')][string]$Version,
  [switch]$Tag,          # create an annotated git tag v<Version> on HEAD (not pushed)
  [switch]$AllowDirty,   # build even if there are uncommitted changes (they are NOT included)
  [switch]$SkipTest      # skip the installer smoke test
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Step($t) { Write-Host "==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    $t" -ForegroundColor Green }
function Fail($t) { Write-Host "    $t" -ForegroundColor Red; exit 1 }

$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$v = "v$Version"
$name = "PocketDeck-$v-windows"
$dist = Join-Path $root "dist"
$zip = Join-Path $dist "$name.zip"
$notes = Join-Path $dist "release-notes-$v.md"
# What ships in the download (paths inside the repo)
$include = @("windows", "installer", "README.md", "LICENSE")
# Must never be inside the zip
$forbidden = @("config.json", "config.json.bak", ".editor_token", "agent.log", "launcher.log")

Write-Host "PocketDeck release build - $v" -ForegroundColor White

# 1. Repository checks --------------------------------------------------------------------
Step "Checking the repository"
git rev-parse --is-inside-work-tree 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Not a git repository: $root" }
$dirty = git status --porcelain
if ($dirty -and -not $AllowDirty) {
  Write-Host ($dirty -join "`n") -ForegroundColor Yellow
  Fail "Uncommitted changes. Commit them first (the zip is built from the last commit), or use -AllowDirty."
}
if ($dirty) { Write-Host "    Uncommitted changes are NOT included in the zip." -ForegroundColor Yellow }
if (git tag --list $v) {
  if ((git rev-parse "$v^{commit}") -ne (git rev-parse HEAD)) { Fail "Tag $v already exists on a different commit." }
}
$head = git rev-parse --short HEAD
Ok "Building from commit $head ($(git log -1 --format=%s))"

# 2. Build the zip --------------------------------------------------------------------------
Step "Building $name.zip"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
if (Test-Path $zip) { Remove-Item $zip -Force }
git archive --format=zip --prefix="$name/" -o $zip HEAD @include
if ($LASTEXITCODE -ne 0) { Fail "git archive failed" }
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
Ok ("{0:N0} KB  sha256 {1}" -f ((Get-Item $zip).Length / 1KB), $sha)

# 3. Verify the contents ---------------------------------------------------------------------
Step "Checking the contents"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("pd-release-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
Expand-Archive $zip $tmp
$files = Get-ChildItem $tmp -Recurse -File -Force
$bad = $files | Where-Object { $forbidden -contains $_.Name -or $_.FullName -match '\\icons\\' -or $_.FullName -match '__pycache__' }
if ($bad) { Remove-Item $tmp -Recurse -Force; Fail ("Private files in the zip: " + (($bad | ForEach-Object Name) -join ", ")) }
foreach ($must in @("windows\deck_agent.py", "windows\config.example.json", "installer\install.ps1", "installer\Install PocketDeck.bat")) {
  if (-not (Test-Path (Join-Path "$tmp\$name" $must))) { Remove-Item $tmp -Recurse -Force; Fail "Missing from the zip: $must" }
}
Ok "$($files.Count) files, no private files"

# 4. Smoke test the installer ------------------------------------------------------------------
if (-not $SkipTest) {
  Step "Smoke-testing the installer from the zip"
  $out = powershell -NoProfile -ExecutionPolicy Bypass -File "$tmp\$name\installer\install.ps1" `
         -Dest "$tmp\installed" -NoLaunch -NoShortcut 2>&1 | Out-String
  $okInstall = (Test-Path "$tmp\installed\config.json") -and (Test-Path "$tmp\installed\deck_agent.py")
  $py = (Get-Command py -ErrorAction SilentlyContinue)
  $compiles = $true
  if ($py) {
    & py -3 -m py_compile "$tmp\installed\deck_agent.py" "$tmp\installed\web_deck.py" "$tmp\installed\actions.py" `
      "$tmp\installed\media.py" "$tmp\installed\icons.py" 2>&1 | Out-Null
    $compiles = ($LASTEXITCODE -eq 0)
  }
  if (-not $okInstall -or -not $compiles) {
    Write-Host $out
    Remove-Item $tmp -Recurse -Force
    Fail "Installer smoke test failed (installed: $okInstall, Python compiles: $compiles)"
  }
  Ok "Installer ran from the zip, starter layout created, Python files compile"
}
Remove-Item $tmp -Recurse -Force

# 5. Draft release notes ------------------------------------------------------------------------
Step "Writing draft release notes"
$prev = git describe --tags --abbrev=0 HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or $prev -eq $v) { $prev = git describe --tags --abbrev=0 "HEAD^" 2>$null }
$range = if ($prev) { "$prev..HEAD" } else { "HEAD" }
$changes = git log $range --no-merges --format="- %s" | Where-Object { $_ -notmatch 'Co-Authored-By|Claude-Session' }
if (-not $changes) { $changes = @("- (no commits since $prev)") }
$since = if ($prev) { " since $prev" } else { "" }
@"
## What's changed$since

$($changes -join "`n")

## Install (Android + Windows)

1. Download **``$name.zip``** below and unzip it.
2. Run **``installer\Install PocketDeck.bat``**. It sets up Python if needed, plus Google's ``adb``, the music add-on and a desktop shortcut.
   Already installed? Run it again: it updates the app and keeps your buttons.
3. On the phone, turn on **USB debugging**, plug in and tap **Allow**. Then open ``http://127.0.0.1:2346`` in Chrome and choose **Add to Home screen > Install**.

Full guide: ``installer/SETUP-ANDROID.txt`` inside the zip.

**SHA-256** ``$name.zip``
``$sha``
"@ | Set-Content -Path $notes -Encoding UTF8
Ok "dist\release-notes-$v.md"

# 6. Optional tag ---------------------------------------------------------------------------------
if ($Tag) {
  Step "Tagging $v"
  if (-not (git tag --list $v)) { git tag -a $v -m "PocketDeck $v"; Ok "Created local tag $v (not pushed)" }
  else { Ok "Tag $v already on this commit" }
}

Write-Host ""
Write-Host "Done. Upload dist\$name.zip and paste dist\release-notes-$v.md into a new GitHub release." -ForegroundColor White
Write-Host "Next:  git push$(if ($Tag) { "; git push origin $v" })   then  GitHub > Releases > Draft a new release > tag $v" -ForegroundColor White
