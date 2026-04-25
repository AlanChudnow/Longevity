@echo off
cd /d "%~dp0"
"C:\Users\Daddy\Anaconda3\python.exe" -m pytest tests/ -v
pause
