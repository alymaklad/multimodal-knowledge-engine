@echo off
REM ---------------------------------------------------------------------------
REM  Multimodal Knowledge Engine - double-click to run.
REM
REM  Opens a console, starts the API, and streams one JSON log line per request
REM  so you can watch the backend work while you use the interface.
REM
REM  Optional: run.bat 8055    to use a different port.
REM  Configuration comes from .env if present; see .env.example.
REM ---------------------------------------------------------------------------

setlocal
title Multimodal Knowledge Engine

REM Run from the project folder no matter where the shortcut is invoked from,
REM because the app writes its database and media under .\data.
cd /d "%~dp0"

set "PORT=8000"
if not "%~1"=="" set "PORT=%~1"

REM Prefer the project virtual environment; fall back to whatever python is on PATH.
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [!] No .venv found - falling back to python on PATH.
    echo     Create one with:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    echo.
    set "PY=python"
)

REM INFO is what makes the per-request log lines appear.
if "%LOG_LEVEL%"=="" set "LOG_LEVEL=INFO"

"%PY%" -c "import uvicorn, fastapi" 2>nul
if errorlevel 1 (
    echo.
    echo [X] Dependencies are missing.
    echo     Install them with:  "%PY%" -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo.
echo   Multimodal Knowledge Engine
echo   ---------------------------------------------------------------
echo   API docs    http://127.0.0.1:%PORT%/docs
echo   Health      http://127.0.0.1:%PORT%/health
echo.
echo   Every request below is one JSON line: method, path, status, ms.
echo   Press Ctrl+C to stop.
echo   ---------------------------------------------------------------
echo.

REM Open the browser once the server is actually accepting connections, so the
REM first page load does not land on a connection error.
start "" /min cmd /c "for /l %%i in (1,1,30) do (curl -s -o nul http://127.0.0.1:%PORT%/health && start "" http://127.0.0.1:%PORT%/docs && exit) & timeout /t 1 /nobreak >nul"

"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --reload --no-access-log

echo.
echo   Server stopped.
pause
