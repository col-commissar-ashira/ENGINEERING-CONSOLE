@echo off
rem Run Engineering Console from source on Windows 10/11 (creates a private virtual environment on first run).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.9 or newer is required: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -c "import PySide6.QtWidgets, PySide6.QtMultimedia, psutil" 2>nul || pip install -r requirements.txt
python main.py %*
