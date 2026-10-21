@echo off
cd /d "%~dp0"
title Team Riders POS - https://www.teamriders.bid
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-online.ps1" %*
pause
