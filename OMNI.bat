@echo off
rem Double-click launcher: installs OMNI on first run, then opens the web dashboard.
cd /d "%~dp0"
python -c "import omni" 2>nul || python -m pip install -e . -q
python -m omni %*
if errorlevel 1 pause
