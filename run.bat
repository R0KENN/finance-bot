@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python not found. Install Python 3.10+ from python.org and tick "Add to PATH".
        goto :end
    )
    set PY=py
) else (
    set PY=python
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto :error
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo File .env created. Put your BOT_TOKEN from @BotFather into it,
    echo save, close Notepad and run run.bat again.
    notepad ".env"
    goto :end
)

echo Starting the bot. Press Ctrl+C to stop.
".venv\Scripts\python.exe" main.py
goto :end

:error
echo.
echo Setup failed. See the messages above.

:end
echo.
pause
