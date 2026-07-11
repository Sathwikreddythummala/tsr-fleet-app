@echo off
rem TSR Enterprises - start the app using the project virtualenv
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Virtualenv not found. Creating it and installing dependencies...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
