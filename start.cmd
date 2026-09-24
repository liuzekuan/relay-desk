@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>&1
if not errorlevel 1 (
    start "" pyw -3 -B "%~dp0meow-local-tools\dashboard.py"
    exit /b
)
python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>&1
if not errorlevel 1 (
    start "" pythonw -B "%~dp0meow-local-tools\dashboard.py"
    exit /b
)
echo Python 3.11+ is required. Install Python, then run start.cmd again.
pause
exit /b 1
