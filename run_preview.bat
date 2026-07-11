@echo off
cd /d "C:\TSR Fleet App"
.venv\Scripts\python.exe -m uvicorn main:app --port 8010
