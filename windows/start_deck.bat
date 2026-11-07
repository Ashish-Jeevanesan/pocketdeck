@echo off
title USB Deck agent
cd /d "%~dp0"
py -3 deck_agent.py
if errorlevel 1 pause
