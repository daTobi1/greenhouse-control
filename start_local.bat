@echo off
cd /d "%~dp0"
echo.
echo  Greenhouse Control – lokale Testumgebung
echo  Dashboard: http://localhost:8080
echo.
venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8080 --reload
