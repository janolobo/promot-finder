@echo off
cd /d "%~dp0"
py -3 bootstrap.py --latest-map
if errorlevel 1 pause
