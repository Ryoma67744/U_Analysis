#!/usr/bin/env bash
# =============================================================================
# MSI Analysis Application - /metrics 定期記録スクリプト (ver60.1)
#
# アプリの /metrics を 1 行 1 レコードで追記し、メモリの増え方を時系列で残す。
#
# なぜ必要か:
#   2026-08-25 の本番障害では、Dash アプリ本体 (python3 run_app.py) の RSS が
#   22 時間で 11.8GB まで膨らみ、mem_limit: 12g の 99.95% に張り付いた。この状態では
#   確保のたびにスワップへの追い出しが起き、コールバック 1 本に 8〜13 秒かかって
#   waitress のスレッド 8 本が全部埋まり、画面が無反応になった
#   （= ユーザーからは「落ちて開かない」に見える）。
#
#   さらに悪いことに memswap_limit: 40g のため OOM kill されず、
#   restart: unless-stopped が永久に発動しない。つまり構成上
#   **「落ちて自動復旧」ではなく「落ちないまま遅くなり続ける」**ことが保証されている。
#
#   障害後にプロセス内キャッシュを全て確認したが、いずれも件数上限付きで、
#   単純に足しても GB 級には届かなかった。つまり **静的にコードを読むだけでは
#   犯人を特定できない**。増え方を実測して「RSS と一緒に何が増えるか」を見るしかない。
#
# 使い方:
#   ./record_metrics.sh                      # 1 回記録して終了
#   METRICS_LOG=/path/to/x.log ./record_metrics.sh
#
#   cron に 5 分おきで登録する (crontab -e):
#     */5 * * * * /home/ubuntu/UMAP-WebApp-ClaudeCode/record_metrics.sh
#
# 読み方:
#   RSS が伸びるとき、一緒に何が増えているかで原因が切り分かる。
#     project_states_size が張り付いたまま RSS だけ伸びる → キャッシュ以外のリーク
#     num_fds が単調増加                                   → FD / パイプの取りこぼし
#     num_threads が単調増加                               → スレッドの回収漏れ
#     diskcache_mb だけ増える                              → ディスク側なので実害は別問題
# =============================================================================
set -uo pipefail

CONTAINER="${METRICS_CONTAINER:-msi-analysis-app}"
LOG="${METRICS_LOG:-${HOME}/msi-metrics.log}"
# ログ自体が新たなディスク圧迫源にならないよう上限を設ける。
# 1 行 ≈ 130 バイト、5 分間隔なら 10MB で約 100 日分。
MAX_BYTES="${METRICS_LOG_MAX_BYTES:-10485760}"

ts="$(date '+%Y-%m-%d %H:%M:%S')"

# コンテナ内から取得する。本番は docker-compose.prod.yml の `ports: !override []` で
# 3838 を公開していないため、ホストから直接 curl しても届かない（実際に空振りした）。
# curl はイメージに入っていない可能性があるので、healthcheck と同じ python3 + urllib を使う。
raw="$(docker exec "$CONTAINER" python3 -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:3838/metrics',timeout=10).read().decode())" \
  2>&1)"
rc=$?

if [ $rc -ne 0 ]; then
    # 取得失敗そのものが情報（コンテナ停止・ハング・再起動中）なので必ず記録する。
    # 1 行に畳んでフォーマットを崩さない。
    reason="$(printf '%s' "$raw" | tr '\n' ' ' | cut -c1-160)"
    printf '%s\tERROR\t%s\n' "$ts" "$reason" >> "$LOG"
else
    # "key=value\n" の羅列を 1 行のタブ区切りに畳む。空行は捨てる。
    line="$(printf '%s' "$raw" | grep -E '^[a-z_]+=' | paste -sd'\t' -)"
    printf '%s\t%s\n' "$ts" "$line" >> "$LOG"
fi

# 上限を超えたら後半半分だけ残す。logrotate に依存せず単体で完結させる
# (この種の運用スクリプトのために logrotate 設定を足すのは大げさなため)。
if [ -f "$LOG" ]; then
    size=$(wc -c < "$LOG")
    if [ "$size" -gt "$MAX_BYTES" ]; then
        total=$(wc -l < "$LOG")
        tail -n $(( total / 2 )) "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    fi
fi
