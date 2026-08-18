# 解析結果 (.rds) の保存形式

`RDS_Files/` に置かれる `.rds` は、**拡張子こそ `.rds` だが中身は qs2 (zstd) バイナリ**。
拡張子を変えないのは Python 側・設定側のパス前提を壊さないため。読み込みは
`load_rds_compact()` がファイル先頭のマジックバイトで判定するので、過去に保存した
gzip / xz / bzip2 / 無圧縮 / 旧 qs のファイルもそのまま読める。

| 形式 | 書き手 | 現在の扱い |
|---|---|---|
| **qs2 (zstd)** | `qs2::qs_save` | **既定**。ver57.1 以降の保存はすべてこれ |
| qs (zstd) | `qs::qsave` | 読めるが書かない。qs2 が無い環境の二番手 |
| gzip / xz / bzip2 / 無圧縮 | `saveRDS` | 読めるだけ。どちらも無い場合の最終手段 |

---

## なぜ qs から qs2 に移したか

**qs 0.27.3 は R 4.6.1 でロードできない。**

```
unable to load shared object '/usr/lib/R/site-library/qs/libs/qs.so':
  undefined symbol: SET_CLOENV
```

`SET_CLOENV` は R の C API シンボルで、新しい R が公開シンボルから外した。
`qs.so` は 2025-03-12 ビルド、R は 4.6.1 (2026-06-24) なので 15 か月の開きがある。
r2u の apt バイナリ `r-cran-qs` は **Candidate = Installed = 0.27.3** で更新が来ないため、
イメージを作り直しても直らない。

その結果 ver50.1 以降、全 `.rds` が gzip の `saveRDS` にフォールバックしていた。
本番実測（1.03 GB の `Step2_HarmonyPCA_Result.rds`）:

| | gzip（ver57.0 まで） | qs2（ver57.1 以降） |
|---|---:|---:|
| 保存 | 162.8 秒 | 10〜20 秒（見込み） |
| 読込 | 29.1 秒 | 5〜15 秒（見込み） |

1 回の解析で Step1 / Step2 / Step2_PCA_uncorrected / Step3 の 4 本を保存するため、
シリアライズだけで毎回 10 分前後を失っていたことになる。

### 同じことを繰り返さないための検査（ver57.2）

この不具合に数か月気づけなかった直接の理由は、**`install_r_packages.R` が
「インストールされたか」しか検査していなかった**ことにある。qs は確かに
インストールされており、`installed.packages()` も dpkg も TRUE を返していた。
壊れるのは `dyn.load` の瞬間だけで、そこを誰も見ていなかった。
しかも既にインストール済みのパッケージは再インストール対象から除外されるため、
壊れたまま固定されていた。

ver57.2 でインストール後に `requireNamespace()` を全パッケージへ通し、
**ロードできないものが 1 つでもあればビルドを失敗させる**ようにした。
`RUN Rscript /app/App/install_r_packages.R` が非ゼロで終わるので、
壊れたイメージが出荷される前に止まる。

> ここでビルドが落ちるようになったら、それは検査が仕事をしている。
> 名指しされたパッケージが本当にロードできない状態なので、検査を外すのではなく
> `apt-cache policy r-cran-<name>` で新しい候補を探すこと。

なお `R CMD INSTALL`（ソースビルド）は install 時にロード検査を行うが、
**apt バイナリ（dpkg）は展開するだけで検査しない**。r2u 経由で入る本構成では、
この検査を自前で持つ必要がある。

---

## 現在どの形式で保存されているか確認する

解析ログに `[rds_io]` 行として必ず残る。

```bash
docker exec msi-analysis-app grep "\[rds_io\]" <出力先>/log/analysis_log.txt | tail -20
```

```
[rds_io] 保存開始: Step2_HarmonyPCA_Result.rds (qs2, nthreads=5)   ← 正常
[rds_io] 保存開始: Step2_HarmonyPCA_Result.rds (saveRDS, compress=gzip)  ← 異常
```

`saveRDS` に落ちている場合は、その直前に理由が出ている。パッケージの生死は直接も見られる。

```bash
docker exec msi-analysis-app Rscript -e 'cat("qs2:", requireNamespace("qs2", quietly=TRUE), "\n")'
docker exec msi-analysis-app Rscript -e 'loadNamespace("qs2")' 2>&1 | grep -v bspm
```

`FALSE` なら、apt に何が来ているかを見る。

```bash
docker exec -u root msi-analysis-app sh -c 'apt-get update -qq && apt-cache policy r-cran-qs2'
```

---

## 既存の `.rds` を qs2 に変換する

gzip で保存された過去のファイルは、読むたびに展開コストを払い続ける。
一括変換ツールがあり、**GUI（RDS 保守モーダル）からも実行できる**。

```bash
# まず dry-run で削減見込みだけ見る（書き込みなし）
docker exec msi-analysis-app Rscript /app/App/Script/helpers/slim_existing_rds.R \
  /app/Data/TIMS/Data --dry-run

# 実行（バックアップ付き）
docker exec msi-analysis-app Rscript /app/App/Script/helpers/slim_existing_rds.R \
  /app/Data/TIMS/Data --backup
```

既に qs / qs2 形式のファイルはスキップされる。書き込みは `<file>.rds.tmp` に行って
成功後に `rename` するアトミック置換なので、途中で落ちても元ファイルは壊れない。
中断しても損はしない（変換済みは次回スキップされる）。

> **解析の実行中に流さないこと。** RDS を読み込むためメモリを使い、走行中の解析を
> 圧迫する。空きメモリの確認方法は [ANALYSIS_HEALTHCHECK.md](ANALYSIS_HEALTHCHECK.md) を参照。

### 実際に踏んだ落とし穴（2026-08 の一括変換にて）

**1. `docker exec` は Ctrl-C で止まらない**

`-t` なしの `docker exec` で Ctrl-C を押すと、終了するのは**ローカルの docker
クライアントだけ**で、コンテナ内のプロセスは走り続ける。止めたつもりのものが
積み上がり、実際に 4 本が並走した。止めるにはコンテナ内の PID を見て `kill` する。

```bash
docker exec msi-analysis-app sh -c 'ps -eo pid,etime,args | grep "[s]lim_existing_rds"'
docker exec msi-analysis-app sh -c 'kill <PID>'
```

**2. `--backup` を並走させてはいけない**

一時ファイルのパスは `<file>.rds.tmp` で**固定**。2 つのプロセスが同じファイルを
同時に処理すると、同じ `.tmp` に交互に書き込み、それを `rename` してしまう
＝**出力が壊れうる**。スキップ判定があるので先行分では衝突しないが、追いつくと当たる。
必ず 1 本だけ走らせること。

**3. 長時間なので切断に強い形で流す**

xz のファイルは 1 件の読み込みに 70〜120 秒かかる。90 件なら 1 時間前後。
SSH が切れても続くよう `nohup` かターミナルマルチプレクサ越しに実行し、
ログをファイルへ落とす。

```bash
nohup docker exec msi-analysis-app Rscript \
  /app/App/Script/helpers/slim_existing_rds.R /app/Data/TIMS/Data --backup \
  > ~/slim_$(date +%Y%m%d_%H%M).log 2>&1 &

grep -c "^\[[0-9]" ~/slim_*.log && tail -2 ~/slim_*.log   # 進捗
```

**4. `.rds.tmp` が残っていたら中断の跡**

対応する `.rds` が無ければ、その解析は保存の途中で死んでいる。消す前に読めるか
試すこと（読めれば `.tmp` を外すだけで復旧する）。

```bash
docker exec msi-analysis-app sh -c 'find /app/Data -name "*.rds.tmp" -exec ls -la {} \;'
```

**5. ver51.0（2026-08-06）以前の解析はステータスが信用できない**

当時は完了処理がブラウザのポーリング callback にしか無く、R が死んでも
`analysis_status.txt` が `finished` のまま残ることがあった。
**Step1 RDS は完走時に削除される**ので、「Step1 が残っているのに finished」は
完走していない証拠になる。次のコマンドで洗い出せる。

```bash
docker exec msi-analysis-app sh -c '
for s in $(find /app/Data -maxdepth 6 -name Step1_SeuratList_Preprocessed.rds 2>/dev/null); do
  rd=$(dirname "$s"); d=$(dirname "$rd")
  st=$(cat "$d/log/analysis_status.txt" 2>/dev/null | head -1)
  n2=$(ls "$rd"/Step2*.rds 2>/dev/null | wc -l)
  printf "%-10s Step2=%s  %s\n" "${st:-(なし)}" "$n2" "$d"
done'
```

`finished` かつ `Step2=0` の行が、**アプリ上は完了に見えるのに結果が無い**フォルダ。
Step1 は counts 込みで残っているので、必要なら `REUMAP_RESUME_DIR` で再開できる。

**6. 変換後の確認は省かない**

`--backup` を付けても、確認前に `.rds.bak` を消してしまうと巻き戻せない。
全件終わってから、まず読めることを確認する。

```bash
docker exec msi-analysis-app Rscript -e '
source("/app/App/Script/helpers/rds_io.R")
fs <- list.files("/app/Data/TIMS/Data", pattern="\\.rds$", recursive=TRUE, full.names=TRUE)
ng <- character()
for (f in fs) if (!isTRUE(tryCatch({invisible(load_rds_compact(f)); TRUE}, error=function(e) FALSE))) ng <- c(ng, f)
cat("検査:", length(fs), "件 / 読めない:", length(ng), "\n")
for (f in ng) cat("  NG:", f, "\n")' 2>&1 | grep -E "検査|NG:"
```

`読めない: 0` を確認し、アプリで数プロジェクト開いてから `.rds.bak` を削除する。

---

## 関連する環境変数

`.env` に書いたうえで `docker-compose.yml` の `environment:` に列挙されている必要がある
（compose の `.env` は `${VAR}` 置換専用なので、列挙しないとコンテナへ届かない）。

| 変数 | 既定 | 用途 |
|---|---|---|
| `QS_NTHREADS` | 自動 | qs2/qs の圧縮スレッド数。`1` にするとネイティブ側のクラッシュを切り分けられる |
| `RDS_FALLBACK_COMPRESS` | `gzip` | qs2 も qs も使えないときの `saveRDS` 圧縮方式（`gzip` / `bzip2` / `xz` / `none`）|

ver57.4 以降、`QS_NTHREADS` 未設定時の既定は
**`min(物理コア - 1, cgroup の CPU 割り当て - 1)`**。`parallel::detectCores()` は
ホストのコア数を返すため、それだけだとコンテナの `cpus:` を超えるスレッドを立てる
（本番の `cpus: '6'` に対し `nthreads=7` が採用されていた）。cgroup が読めない
環境では従来どおり物理コア - 1 になる。

`RDS_FALLBACK_COMPRESS=none` は圧縮 CPU をゼロにする代わりにファイルが 2〜3 倍になる。
「保存が遅いのは圧縮のせいか」を切り分けるとき、または CPU とメモリが逼迫していて
ディスクに余裕がある環境で使う。

> `xz` は**常用形式で展開が最も遅い**。ver50.1 で既定から外した（1.00 GB の展開に
> 118.7 秒＝抽出全体の 51%）。圧縮率のために選ばないこと。
