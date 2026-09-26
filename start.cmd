@echo off
rem Dubbelklickbar start för Windows. Anropar bara start.py.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 start.py %*
) else (
    python start.py %*
)

rem Håll fönstret öppet om något gick fel, annars ser man aldrig felmeddelandet.
if errorlevel 1 (
    echo.
    pause
)
