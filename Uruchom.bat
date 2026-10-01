@echo off
cd /d "%~dp0"
py -3 bootstrap.py
if errorlevel 1 goto error
exit /b
:error
echo Nie udalo sie uruchomic programu. Sprawdz instalacje Python 3.11 lub nowszej i polaczenie internetowe.
pause
