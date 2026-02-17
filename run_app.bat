@echo off
REM MSI Analysis Application Launcher (Python Dash version)
REM 質量分析イメージングデータ解析アプリケーション

echo Starting MSI Analysis Application...
echo.

REM Change to app root directory
cd /d "%~dp0"

REM Launch Python Dash app
python Other\run_app.py

pause
