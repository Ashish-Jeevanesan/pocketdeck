@echo off
set "S=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\USBDeck.bat"
> "%S%" echo @echo off
>> "%S%" echo start "USB Deck agent" /min "%~dp0start_deck.bat"
echo USB Deck agent will now start automatically when you sign in to Windows.
echo (To undo: delete "%S%")
pause
