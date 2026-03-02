@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM  MSI Analysis Application - 初回セットアップ
REM  外部共有用：このバッチファイルを実行して環境を構築します
REM ============================================================

echo ============================================================
echo   MSI Analysis Application - Setup
echo ============================================================
echo.

cd /d "%~dp0"

REM ============================================================
REM  Step 1: Python チェック
REM ============================================================
echo [1/4] Python の確認中...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [エラー] Python が見つかりません。
    echo   Python 3.10 以上をインストールしてください。
    echo   ダウンロード: https://www.python.org/downloads/
    echo.
    echo   ※ インストール時に「Add Python to PATH」にチェックを入れてください。
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do echo   %%i が見つかりました。
echo.

REM ============================================================
REM  Step 2: Python パッケージインストール
REM ============================================================
echo [2/4] Python パッケージをインストール中...
echo   （初回は数分かかる場合があります）
echo.
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [エラー] Python パッケージのインストールに失敗しました。
    echo   ネットワーク接続を確認してください。
    echo.
    pause
    exit /b 1
)
echo.
echo   Python パッケージのインストールが完了しました。
echo.

REM ============================================================
REM  Step 3: R チェック
REM ============================================================
echo [3/4] R の確認中...
Rscript --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [スキップ] R が見つかりません。
    echo   R はクラスター解析等の高度な解析で使用します。
    echo   ビューア機能のみ使用する場合は R は不要です。
    echo.
    echo   R をインストールする場合:
    echo     ダウンロード: https://cran.r-project.org/bin/windows/base/
    echo     インストール後、再度 setup.bat を実行してください。
    echo.
    goto :DONE
)
for /f "tokens=*" %%i in ('Rscript --version 2^>^&1') do echo   R が見つかりました: %%i
echo.

REM ============================================================
REM  Step 4: R パッケージインストール
REM ============================================================
echo [4/4] R パッケージをインストール中...
echo   （初回は 10〜20 分かかる場合があります）
echo.
Rscript install_r_packages.R
if errorlevel 1 (
    echo.
    echo [警告] 一部の R パッケージのインストールに失敗した可能性があります。
    echo   詳細は上記のログを確認してください。
    echo.
)
echo.

:DONE
echo ============================================================
echo   セットアップが完了しました！
echo.
echo   アプリの起動方法:
echo     run_app.bat をダブルクリックしてください。
echo ============================================================
echo.
pause
