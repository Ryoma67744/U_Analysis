@echo off
chcp 65001 >nul 2>&1
REM MSI Analysis Application Launcher (Python Dash version)
REM 質量分析イメージングデータ解析アプリケーション

echo Starting MSI Analysis Application...
echo.

REM Change to App folder (sibling of this script)
cd /d "%~dp0App"

REM Launch Python Dash app
python run_app.py

pause
