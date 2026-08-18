# 解析が止まっているかを確認する

「画面の進捗バーが動かない」ときに、それが

- **(a) 本当に止まっている**（プロセスが消えた）のか
- **(b) 重い工程を計算中で、ログが伸びていないだけ**なのか

を切り分けるための手順。この 2 つは見え方が同じなのに対処が正反対で、
(b) を (a) と誤診すると**完走間近の 2 時間の解析を自分で潰す**ことになる。

---

## 1. まず何を見るか

判断材料は 3 つある。1 つだけでは決められない。

| 見るもの | 意味 |
|---|---|
| **PID が生きているか** | 消えていれば「停止」で確定。ここだけは一発で分かる |
| **CPU 使用率** | プロセスが居ても CPU 0% が続くなら本当のハングを疑う |
| **ログ / 出力ファイルの最終更新** | どちらかが動いていれば進行中 |

ログだけを見てはいけない。R は PNG や RDS を書く工程で何も出力しないため、
「10 分ログが伸びない＝停止」と判断すると正常な解析を誤検出する。
逆に RPCA / UMAP / DEG は**無言で 30 分以上**かかることがある。

解析の実体（PID・ログ）は次の場所にある。

```
<出力フォルダ>/log/
    analysis_job.json      … 実行中の台帳（PID・開始時刻・完了処理済みか）
    analysis_status.txt    … running / finished / error / stopped
    analysis_log.txt       … R の標準出力。末尾に [EXIT] 行が出ることがある
    analysis_progress.txt  … 進捗バー用
    history/               … 過去の実行分（各 20 件まで）
```

---

## 2. PowerShell から確認する（推奨）

リポジトリ直下の `check_analysis.ps1` が上の 3 つをまとめて取ってくる。
実行場所（Docker コンテナ内 / Windows ローカル）は自動で判定する。

```powershell
cd <リポジトリのフォルダ>
.\check_analysis.ps1
```

実行ポリシーで弾かれる場合は、そのウィンドウだけ許可する:

```powershell
powershell -ExecutionPolicy Bypass -File .\check_analysis.ps1
```

主なオプション:

```powershell
.\check_analysis.ps1 -StallMinutes 60   # 停滞と見なす無更新時間（既定 30 分）
.\check_analysis.ps1 -Tail 50           # ログ表示行数（既定 15）
.\check_analysis.ps1 -RunningOnly       # 完了処理が済んでいないジョブだけ
.\check_analysis.ps1 -Mode local        # コンテナを見ずにローカルを見る
.\check_analysis.ps1 -Json              # 機械可読（監視から叩く用）
```

### 出力の読み方

| 判定 | 意味 | 対処 |
|---|---|---|
| **実行中** | プロセスが居て、ログか出力が動いている | 待つ |
| **停滞の疑い** | プロセスは居るが更新が止まっている | CPU 使用率を見る。0% でなければ計算中 |
| **停止しています** | 未完了なのにプロセスが居ない | 再実行が必要。原因を下記で特定する |
| **完了 / エラーで終了 / 利用者が停止** | 終了済み | ログ末尾を確認 |

終了コードも同じ区分で返るので、監視やタスクスケジューラから使える。

| 終了コード | 意味 |
|---|---|
| 0 | 実行中（正常）または終了済み |
| 3 | 停止している |
| 4 | 停滞の疑い |
| 5 | 実行中の解析が見つからない |
| 1 | 確認自体に失敗 |

コンテナ内・Linux サーバー上で直接動かす場合は、中身の Python を呼べばよい。

```bash
docker exec msi-analysis-app python3 /app/App/tools/analysis_status_report.py
docker exec msi-analysis-app python3 /app/App/tools/analysis_status_report.py --json
```

---

## 3. 「停止しています」と出たときの原因特定

無言で消える原因はほぼ次の 3 つ。**ログ末尾の `[EXIT]` 行**が最初の手掛かりになる。

```
[EXIT] R プロセスは シグナル SIGKILL(9) による強制終了 で終了しました。
```

| 手掛かり | 原因 | 対処 |
|---|---|---|
| `SIGKILL(9)` + コンテナの `OOMKilled=true` | メモリ不足でカーネルが強制終了 | `docker-compose.yml` の `mem_limit` を見直す。`.env` の `R_MAX_VSIZE_GB` を設定すると、OOM の代わりに R の明示エラーで止まる |
| `SIGKILL(9)` + コンテナ再起動あり | `docker compose up -d --build` などでコンテナごと再起動した | 解析中の再ビルドを避ける |
| `SIGTERM(15)` | 停止操作、または `R_ANALYSIS_TIMEOUT_SEC` 超過 | `.env` で上限を延ばす（0 で無効） |
| `[EXIT]` 行が無く、ログが途中で終わっている | アプリ（PID 1）ごと消えたため終了記録を書けなかった | 下のコンテナ側の確認へ |

```powershell
docker inspect msi-analysis-app --format "{{.State.Status}} OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}} Started={{.State.StartedAt}}"
docker logs --tail 200 msi-analysis-app
```

`Started` が解析の開始時刻より**後**なら、解析中にコンテナが再起動している。

なお「停止しています」の状態は、アプリを開き直した時点で
`analysis_status.txt` が `error` に書き換わる（起動時の後始末）。
確認より先に再起動すると、この判定材料が消えることに注意。

---

## 4. スクリプトを使わずに素の PowerShell で見る

### Docker 運用の場合

```powershell
# コンテナと R プロセス
docker inspect msi-analysis-app --format "{{.State.Status}} OOMKilled={{.State.OOMKilled}}"
docker exec msi-analysis-app ps -eo pid,etime,pcpu,rss,args --sort=-pcpu | Select-Object -First 8

# 台帳と状態をまとめて
docker exec msi-analysis-app sh -c 'for f in $(find /app/Data -maxdepth 6 -name analysis_job.json); do d=$(dirname "$f"); echo "--- $d"; cat "$d/analysis_status.txt"; echo; tail -3 "$d/analysis_log.txt"; done'
```

> Unix の `Rscript` は exec 後にプロセス名が `R` になる。`ps` で `rscript` を
> 探しても見つからないのはそのため。`args` 列でスクリプト名を見ること。

### Windows ローカル実行の場合

```powershell
# R プロセスの生存と CPU 時間
Get-Process Rscript, Rterm, R -ErrorAction SilentlyContinue |
    Select-Object Id, CPU, @{n = 'RSS(MB)'; e = { [int]($_.WorkingSet64 / 1MB) } }, StartTime

# 最新の解析ログの更新時刻と末尾
$log = Get-ChildItem .\Data -Recurse -Filter analysis_log.txt -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
"{0}（{1:N1} 分前に更新）" -f $log.FullName, (New-TimeSpan -Start $log.LastWriteTime).TotalMinutes
Get-Content $log.FullName -Tail 20

# 進行をそのまま眺める（Ctrl+C で終了）
Get-Content $log.FullName -Tail 20 -Wait
```

`CPU`（累積 CPU 秒）を数十秒あけて 2 回見るのが、
「計算中」と「ハング」を分ける一番手軽な方法。増えていれば動いている。

---

## 5. 誤検出を減らす

`-StallMinutes` の既定 30 分は、無言で長くかかる工程に対しては短いことがある。
`check_analysis.ps1` は CPU を食っていれば「実行中」と判定するため通常は問題にならないが、
`psutil` が無い環境では CPU を測れず「停滞の疑い」に倒れる。
その場合は閾値を伸ばすか、CPU 時間を自分で 2 回見て判断する。

```powershell
.\check_analysis.ps1 -StallMinutes 90
```
