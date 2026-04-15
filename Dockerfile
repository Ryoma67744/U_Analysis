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
COPY install_r_packages.R /app/
RUN Rscript install_r_packages.R

# Python パッケージインストール
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# アプリケーションコードをコピー
COPY . /app

# 永続化ディレクトリを作成し権限を付与
RUN mkdir -p /app/app/sessions /app/app/projects /app/app/projects/backups \
    /app/app/presets /app/app/shares /app/logs /app/cache \
    /app/data/DESI/Data /app/data/TIMS/Data /app/output \
    && chown -R msiapp:msiapp /app

# 環境変数
ENV R_HOME=/usr/lib/R
ENV PYTHONUNBUFFERED=1
ENV APP_HOST=0.0.0.0
ENV APP_PORT=3838

# 非rootユーザーで実行
USER msiapp

EXPOSE 3838

CMD ["python3", "run_app.py"]
