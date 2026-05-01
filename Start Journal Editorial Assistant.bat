@echo off
setlocal

cd /d "%~dp0"

set APP_URL=http://localhost:8501/
set INSTALL_MARKER=.venv\.requirements-installed

echo Starting Journal Editorial Assistant...
echo Project folder: %cd%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found.
  echo Please install Python 3 from https://www.python.org/downloads/ and check "Add python.exe to PATH" during installation.
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo Creating local Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Could not create the Python environment.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"

if not exist "%INSTALL_MARKER%" (
  echo First launch: installing required packages...
  python -m pip install --upgrade pip
  if errorlevel 1 (
    echo Could not update pip.
    pause
    exit /b 1
  )
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Could not install required packages.
    pause
    exit /b 1
  )
  type nul > "%INSTALL_MARKER%"
) else (
  echo Using existing local Python environment.
)

echo.
echo Opening app at %APP_URL%
echo Keep this Command Prompt window open while using the app.
echo.

start "" "%APP_URL%"

python -m streamlit run app.py --server.port 8501 --server.address localhost

echo.
echo The app stopped.
pause
