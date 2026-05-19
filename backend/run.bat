@echo off
echo Starting Argo Nexus Data Pipeline...

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Ensure the server starts with unbuffered output
set PYTHONUNBUFFERED=1

echo Running on http://127.0.0.1:8000
python -m uvicorn main:app --host 127.0.0.1 --port 8000
