# =============================================================================
# MSI Analysis Application - Dockerfile
# Python + R/Seurat コンテナ定義
# =============================================================================

FROM rocker/r-ver:4.4.2

# システム依存パッケージ（R/Python パッケージのビルドに必要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    libcurl4-openssl-dev libssl-dev libxml2-dev \
    libharfbuzz-dev libfribidi-dev libfreetype6-dev \
    libpng-dev libtiff5-dev libjpeg-dev \
    libfontconfig1-dev libgit2-dev \
    cmake pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 非rootユーザー作成
RUN useradd -m -s /bin/bash msiapp

WORKDIR /app

# R パッケージインストール（キャッシュ効率のため先に実行、最も時間がかかるレイヤー）
COPY App/install_r_packages.R /app/App/
RUN Rscript /app/App/install_r_packages.R

# Python パッケージインストール
COPY App/requirements.txt /app/App/
RUN pip3 install --no-cache-dir --break-system-packages -r /app/App/requirements.txt

# アプリケーション全体をコピー (App/ + Data/ + ルート設定ファイル)
COPY . /app

# 永続化ディレクトリを作成し権限を付与 (Data/ 配下: DESI/TIMS 入力 + Other/ 内部データ)
RUN mkdir -p \
    /app/Data/DESI/Data /app/Data/TIMS/Data \
    /app/Data/Other/Common \
    /app/Data/Other/sessions \
    /app/Data/Other/projects /app/Data/Other/projects/backups \
    /app/Data/Other/presets \
    /app/Data/Other/shares \
    /app/Data/Other/cache \
    /app/Data/Other/logs \
    /app/Data/Other/output \
    && chown -R msiapp:msiapp /app

# アプリの作業ディレクトリ
WORKDIR /app/App

# 環境変数
ENV R_HOME=/usr/lib/R
ENV PYTHONUNBUFFERED=1
ENV APP_HOST=0.0.0.0
ENV APP_PORT=3838

# 非rootユーザーで実行
USER msiapp

EXPOSE 3838

CMD ["python3", "run_app.py"]
