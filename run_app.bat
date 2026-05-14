@echo off
echo ======================================================
echo   Argo Nexus - Clean Startup Script
echo ======================================================

echo [1/3] Killing old Python and Node processes...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM node.exe /T >nul 2>&1

cd /d "%~dp0"

echo [2/3] Starting Backend Server (Uvicorn)...
start "Argo-Nexus-Backend" cmd /k "cd backend && uvicorn main:app --reload"

echo [3/3] Starting Frontend Server (React)...
start "Argo-Nexus-Frontend" cmd /k "cd frontend && npm start"

echo.
echo ======================================================
echo   Servers are starting in separate windows.
echo   You can close this window now.
echo ======================================================
timeout /t 5
