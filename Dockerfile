# =============================================================================
# MSI Analysis Application - Dockerfile (r2u 版)
# R パッケージは apt バイナリで取得 → 高速 & システム lib 自動解決
# =============================================================================

FROM rocker/r2u:noble

# Python 関連のみ apt で入れる (R 関連は bspm が apt で自動解決)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 非rootユーザー作成
RUN useradd -m -s /bin/bash msiapp

WORKDIR /app

# Bioconductor の準備 (mutoss は multtest に依存)
# r2u の apt 索引は必ず直前に更新する（古い索引のまま bspm→apt 解決すると
# gtable/rlang 等の依存が取りこぼされ configure 失敗→ビルド落ちするため）。
RUN apt-get update && Rscript -e 'install.packages("BiocManager"); BiocManager::install("multtest", update = FALSE, ask = FALSE)' && rm -rf /var/lib/apt/lists/*

# R パッケージインストール (r2u が apt バイナリから自動取得 → 5 分以内)
# 同上: install 直前に apt-get update を入れて索引陳腐化での依存解決崩れを防止。
COPY App/install_r_packages.R /app/App/
RUN apt-get update && Rscript /app/App/install_r_packages.R && rm -rf /var/lib/apt/lists/*

# Python パッケージインストール
COPY App/requirements.txt /app/App/
RUN pip3 install --no-cache-dir --break-system-packages -r /app/App/requirements.txt

# アプリケーション全体をコピー
COPY . /app

# 永続化ディレクトリを作成し権限を付与
RUN mkdir -p \
    /app/Data/DESI/Data /app/Data/TIMS/Data \
    /app/Data/Other/Common \
    /app/Data/Other/common \
    /app/Data/Other/sessions \
    /app/Data/Other/projects /app/Data/Other/projects/backups \
    /app/Data/Other/presets \
    /app/Data/Other/shares \
    /app/Data/Other/cache \
    /app/Data/Other/logs \
    /app/Data/Other/output \
    && chown -R msiapp:msiapp /app

WORKDIR /app/App

ENV R_HOME=/usr/lib/R
ENV PYTHONUNBUFFERED=1
ENV APP_HOST=0.0.0.0
ENV APP_PORT=3838

USER msiapp

EXPOSE 3838

CMD ["python3", "run_app.py"]
