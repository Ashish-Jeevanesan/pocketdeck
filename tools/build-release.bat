@echo off
rem Build a PocketDeck release zip.  Usage:  build-release.bat 1.0.1
if "%~1"=="" (echo Usage: %~nx0 ^<version^>   e.g. %~nx0 1.0.1 & exit /b 1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-release.ps1" -Version %*
