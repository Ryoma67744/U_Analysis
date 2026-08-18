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

> **解析の実行中に流さないこと。** RDS を読み込むためメモリを使い、走行中の解析を
> 圧迫する。空きメモリの確認方法は [ANALYSIS_HEALTHCHECK.md](ANALYSIS_HEALTHCHECK.md) を参照。

---

## 関連する環境変数

`.env` に書いたうえで `docker-compose.yml` の `environment:` に列挙されている必要がある
（compose の `.env` は `${VAR}` 置換専用なので、列挙しないとコンテナへ届かない）。

| 変数 | 既定 | 用途 |
|---|---|---|
| `QS_NTHREADS` | `detectCores-1` | qs2/qs の圧縮スレッド数。`1` にするとネイティブ側のクラッシュを切り分けられる |
| `RDS_FALLBACK_COMPRESS` | `gzip` | qs2 も qs も使えないときの `saveRDS` 圧縮方式（`gzip` / `bzip2` / `xz` / `none`）|

`RDS_FALLBACK_COMPRESS=none` は圧縮 CPU をゼロにする代わりにファイルが 2〜3 倍になる。
「保存が遅いのは圧縮のせいか」を切り分けるとき、または CPU とメモリが逼迫していて
ディスクに余裕がある環境で使う。

> `xz` は**常用形式で展開が最も遅い**。ver50.1 で既定から外した（1.00 GB の展開に
> 118.7 秒＝抽出全体の 51%）。圧縮率のために選ばないこと。
