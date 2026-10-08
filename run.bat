@echo off
rem Start JalTaap with the Python that has its packages (not the MSYS2 one on PATH).
cd /d "%~dp0"
set "PY=C:\Users\my060\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if exist "%PY%" goto have
where py >nul 2>nul
if errorlevel 1 (
  echo Could not find Python 3. Install it from python.org and try again.
  pause
  exit /b 1
)
set "PY=py -3"
:have
%PY% -c "import pandas, fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo Installing packages, first run only...
  %PY% -m pip install -r requirements.txt
)
echo Starting JalTaap - open http://localhost:8000
%PY% server.py
pause
