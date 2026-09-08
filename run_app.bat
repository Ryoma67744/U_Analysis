@echo off
chcp 65001 >nul 2>&1
REM MSI Analysis Application Launcher (Python Dash version)
REM 質量分析イメージングデータ解析アプリケーション

echo Starting MSI Analysis Application...
echo.

REM Change to App folder (sibling of this script)
cd /d "%~dp0App"

REM ★ ver63.4: 初回起動時に App\.env を用意する（FLASK_SECRET_KEY 等の自動生成）。
REM   .env は .gitignore 済みで、ZIP を展開しただけの環境には存在しない。
REM   その状態で起動すると FLASK_SECRET_KEY 未設定の RuntimeError で即死していた。
REM   既に .env があれば何もしない（設定済みの値は書き換えない）。
python -m app.services.env_bootstrap
if errorlevel 1 (
    echo [警告] .env の自動生成に失敗しました。App\.env を手動で用意してください。
    echo.
)

REM Launch Python Dash app
python run_app.py

pause
