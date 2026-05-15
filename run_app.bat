@echo off
setlocal enabledelayedexpansion
echo ======================================================
echo   Argo Nexus - Production Startup Script
echo ======================================================
echo.

cd /d "%~dp0"

echo [1/5] Cleaning up old background processes...
echo Killing any lingering backend (port 8000)...
FOR /F "tokens=5" %%T IN ('netstat -a -n -o ^| findstr :8000') DO (
    IF NOT "%%T"=="0" (
        taskkill /PID %%T /F >nul 2>&1
    )
)

echo Killing any lingering frontend (port 3000)...
FOR /F "tokens=5" %%T IN ('netstat -a -n -o ^| findstr :3000') DO (
    IF NOT "%%T"=="0" (
        taskkill /PID %%T /F >nul 2>&1
    )
)
echo Old processes terminated.
echo.

echo [2/5] Checking Python virtual environment...
if not exist "backend\venv\Scripts\activate.bat" (
    echo Creating Python venv at backend\venv...
    cd backend
    python -m venv venv
    cd ..
)

echo [3/5] Updating Backend Dependencies...
call backend\venv\Scripts\activate.bat
cd backend
pip install -r requirements.txt
cd ..
echo.

echo [4/5] Checking frontend dependencies...
if not exist "frontend\node_modules" (
    echo Installing frontend dependencies...
    cd frontend
    call npm install
    cd ..
)
echo.

echo [5/5] Starting Servers...
echo Starting Backend Server (Uvicorn on port 8000)...
start "Argo-Nexus-Backend" cmd /k "cd backend && call venv\Scripts\activate && python -m uvicorn main:app --host 0.0.0.0 --port 8000"

echo Starting Frontend Server (React on port 3000)...
start "Argo-Nexus-Frontend" cmd /k "cd frontend && npm start"

echo.
echo ======================================================
echo   Servers are starting in separate windows.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:3000
echo   Health:   http://localhost:8000/api/health
echo ======================================================
timeout /t 5
