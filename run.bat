@echo off
REM One-command start for Windows
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt
if not exist .env copy .env.example .env
echo Open http://localhost:8000
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
