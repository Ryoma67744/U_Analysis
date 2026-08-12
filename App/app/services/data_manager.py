# =============================================================================
# MSI Analysis Application - Data Manager
# データ管理モジュール
# =============================================================================

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("msi.data_manager")


def list_msi_files(data_folder: str) -> list[str]:
    """データフォルダ内のMSIファイル一覧を取得（拡張子なし）。

    DESI 生データは従来 `.txt` のみだが、同一レイアウトの Excel(.xlsx/.xls) /
    CSV(.csv) も登録できるよう、対応する `.txt` が無い stem については
    変換元 (.csv/.xlsx/.xls) の stem も一覧に含める（解析時に自動で .txt 化）。
    同一 stem に `.txt` がある場合は `.txt` を優先（重複させない）。
    """
    folder = Path(data_folder)
    if not folder.is_dir():
        return []
    txt_stems = {f.stem for f in folder.glob("*.txt")}
    from app.services.desi_converter import DESI_SRC_EXTS
    extra_stems = {
        f.stem for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in DESI_SRC_EXTS
    } - txt_stems
    return sorted(txt_stems | extra_stems)


_PARQUET_EXTS = {".parquet", ".pq"}
_CSV_EXTS = {".csv", ".tsv", ".txt"}
# 注釈サイドカー（1 feature 1 行のメタ表）は解析/エクスポートの「サンプル」ではないため除外する。
_SIDECAR_SUFFIX = "_feature_annotations.parquet"


def _is_sidecar(f: Path) -> bool:
    return f.name.endswith(_SIDECAR_SUFFIX)


def _filter_tims_candidates(folder: Path) -> list[Path]:
    """TIMS 解析対象候補ファイルを優先度ルールで絞り込む。

    - Parquet (.parquet/.pq) が 1 本以上あれば Parquet のみを返す
    - それ以外は CSV/TSV/TXT を返す
    - `*_feature_annotations.parquet`（注釈サイドカー）はサンプルではないので常に除外
    - いずれもソート済み
    """
    if not folder.is_dir():
        return []
    parquets = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _PARQUET_EXTS and not _is_sidecar(f)
    )
    if parquets:
        csv_count = sum(
            1 for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in _CSV_EXTS
        )
        if csv_count:
            logger.debug(
                "Parquet 優先: %d 件の CSV/TSV/TXT をサンプル候補から除外 (%s)",
                csv_count, folder,
            )
        return parquets
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _CSV_EXTS
    )


def list_tims_files(data_folder: str) -> list[str]:
    """データフォルダ内のTIMSファイル一覧を取得（拡張子なし）

    対応形式: .parquet, .pq, .csv, .tsv, .txt
    優先度: Parquet があれば Parquet のみ、無ければ CSV/TSV/TXT
    """
    return [f.stem for f in _filter_tims_candidates(Path(data_folder))]


def build_tims_input_paths(data_folder: str) -> list[str]:
    """データフォルダ内のTIMSファイルのフルパスリストを返す。

    Rスクリプトの INPUT_PATHS / ORIGINAL_INPUT_PATHS に対応。
    優先度: Parquet があれば Parquet のみ、無ければ CSV/TSV/TXT
    """
    return [str(f) for f in _filter_tims_candidates(Path(data_folder))]


def list_tims_files_multi(data_folders: list[str]) -> list[str]:
    """複数フォルダからTIMSファイル一覧を取得（拡張子なし、重複除去）。"""
    all_files = []
    seen = set()
    for folder in data_folders:
        for f in list_tims_files(folder):
            if f not in seen:
                seen.add(f)
                all_files.append(f)
    return all_files


def build_tims_input_paths_multi(data_folders: list[str]) -> list[str]:
    """複数フォルダからTIMSファイルのフルパスリストを返す。"""
    all_paths = []
    for folder in data_folders:
        all_paths.extend(build_tims_input_paths(folder))
    return sorted(all_paths)


def read_raw_mz_spectrum(data_folder: str, is_tims: bool = True,
                         sample_name: str = None) -> Optional[pd.DataFrame]:
    """生データファイルから m/z フィーチャーの平均スペクトルを読み取る。

    data_folder 内の最初の1ファイルを読み込み、
    ``expression_matrix.parquet`` と同等の形式（列名 = フィーチャー名、値 = 平均強度）で返す。
    キャリブレーション自動検出のフォールバック用。

    Parameters
    ----------
    sample_name : str, optional
        指定時はそのサンプル名（ファイルstem）に一致するファイルのみ読み込む。

    Returns
    -------
    pd.DataFrame | None
        1行 × N列 の DataFrame（各列 = フィーチャー名、値 = 平均強度）。
        読み取り失敗時は None。
    """
    import re as _re

    folder = Path(data_folder) if data_folder else None
    if not folder or not folder.is_dir():
        return None

    try:
        if is_tims:
            return _read_tims_raw(folder, sample_name=sample_name)
        else:
            # ver51.8: DESI にも sample_name を渡す（従来は捨てていた）
            return _read_desi_raw(folder, sample_name=sample_name)
    except Exception:
        return None


def _is_numeric(s: str) -> bool:
    """文字列が数値として解釈できるか判定する。"""
    try:
        float(s)
        return True
    except ValueError:
        return False


def _read_mz_sorted_metadata(pf) -> Optional[list]:
    """Parquet schema メタデータ `mz_sorted`（カンマ区切りのフル桁 m/z）を返す。

    SCiLS 変換で feature 列名にパイプ全文（化合物名）が埋め込まれても、
    m/z 値はこのメタデータ（列順）から確実に復元できる。
    """
    try:
        md = pf.schema_arrow.metadata or {}
        raw = md.get(b"mz_sorted")
        if raw is None:
            return None
        return [float(x) for x in raw.decode("utf-8").split(",") if x.strip()]
    except Exception:
        return None


def _mz_from_embedded_name(colname: str) -> Optional[float]:
    """埋め込み列名（`化合物名_<m/z> | …`）の先頭から m/z 数値を抽出（メタ無し時の保険）。"""
    import re as _re
    head = str(colname).split("|", 1)[0]
    m = _re.search(r"_(\d+(?:\.\d+)?)\s*$", head.strip())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _read_tims_raw(folder: Path, sample_name: str = None) -> Optional[pd.DataFrame]:
    """TIMS 生データ（parquet/csv/tsv/txt）から mz_ 列の平均を取得。"""
    import re as _re

    # Parquet 優先フィルタを適用 (Spot/Annotation CSV 等の中間ファイルを除外)
    files = _filter_tims_candidates(folder)
    if not files:
        return None

    # ★ ver51.8: サンプル指定時に一致が無ければ **先頭ファイルへ落とさない**。
    #   従来は `matched[0] if matched else files[0]` で、利用者が
    #   キャリブレーション対象サンプルを明示的に選んでいても、そのファイルが
    #   候補集合に無ければ黙って別サンプルのスペクトルを返していた。
    #   キャリブレーションはその結果で ppm ドリフトを決めるので、
    #   **サンプル A の補正曲線がサンプル B の測定値から作られる**。
    #   指定が満たせないなら None を返し、呼び出し側にエラーを出させる。
    if sample_name:
        matched = [f for f in files if f.stem == sample_name]
        if not matched:
            logger.warning(
                "指定サンプル '%s' が %s に見つかりません（候補: %s）。"
                "別ファイルへのフォールバックはしません。",
                sample_name, folder, [f.stem for f in files][:10])
            return None
        fp = matched[0]
    else:
        fp = files[0]
    ext = fp.suffix.lower()

    if ext in (".parquet", ".pq"):
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(fp)
        all_names = pf.schema.names
        non_meta = {"id", "x", "y", "annotation"}
        # 旧形式: mz_ 接頭辞 / 裸の数値列名
        mz_cols = [n for n in all_names if n.startswith("mz_")]
        if not mz_cols:
            mz_cols = [n for n in all_names
                       if n not in non_meta and _is_numeric(n)]
        if mz_cols:
            df = pd.read_parquet(fp, columns=mz_cols)
        else:
            # 埋め込み列名（パイプ全文）: feature 列を除外法で特定し、m/z 値は
            # mz_sorted メタデータ（列順）から取得して mz_ 列名に正規化する。
            feature_cols = [n for n in all_names if n not in non_meta]
            if not feature_cols:
                return None
            df = pd.read_parquet(fp, columns=feature_cols)
            mz_vals = _read_mz_sorted_metadata(pf)
            if mz_vals is not None and len(mz_vals) == len(feature_cols):
                df.columns = [f"mz_{m:.4f}" for m in mz_vals]
            else:
                df.columns = [
                    (f"mz_{m:.4f}" if (m := _mz_from_embedded_name(c)) is not None else c)
                    for c in feature_cols
                ]
    else:
        # CSV / TSV / TXT — ヘッダーあり前提
        sep = "\t" if ext in (".tsv", ".txt") else ","
        df = pd.read_csv(fp, sep=sep)
        mz_cols = [c for c in df.columns if _re.match(r"mz_\d", c)]
        if not mz_cols:
            return None
        df = df[mz_cols]

    # 1行の平均スペクトル DataFrame を返す
    avg = df.mean().to_frame().T
    return avg


def _read_desi_raw(folder: Path, sample_name: str = None) -> Optional[pd.DataFrame]:
    """DESI 生データ（.txt）から m/z 値と平均強度を取得。

    ★ ver51.8: `sample_name` を受け取るようにした。従来は引数に存在すらせず、
      `read_raw_mz_spectrum` が TIMS にだけ渡していたため、DESI では利用者が
      どのサンプルを選んでも **常に先頭の .txt** を読んでいた。

    DESI .txt フォーマット:
      行1: 空行
      行2: ヘッダー情報
      行3: 列インデックス
      行4: m/z 値（タブ区切り）
      行5: フラグメント m/z（使用しない）
      行6〜: ピクセルデータ（ID, x, y, 強度..., line, pixel）
    """
    import re as _re

    txt_files = sorted(f for f in folder.iterdir() if f.suffix.lower() == ".txt")
    if not txt_files:
        return None

    # TIMS 側と同じ規約: 指定があれば厳密一致、無ければ None（先頭へ落とさない）
    if sample_name:
        matched = [f for f in txt_files if f.stem == sample_name]
        if not matched:
            logger.warning(
                "指定サンプル '%s' が %s に見つかりません（候補: %s）。"
                "別ファイルへのフォールバックはしません。",
                sample_name, folder, [f.stem for f in txt_files][:10])
            return None
        fp = matched[0]
    else:
        fp = txt_files[0]
    with open(fp, "r", encoding="utf-8") as fh:
        lines = [fh.readline() for _ in range(5)]

    # 行4 (0-indexed: lines[3]) から m/z 値を抽出
    mz_line = lines[3].strip()
    if not mz_line:
        return None
    parts = mz_line.split("\t")
    # 先頭に空フィールドがある場合をスキップ
    mz_values = []
    col_indices = []
    for i, p in enumerate(parts):
        p = p.strip()
        if p and _re.match(r"\d+\.?\d*$", p):
            mz_values.append(float(p))
            col_indices.append(i)

    if not mz_values:
        return None

    # データ行を読み込み（行6〜、skiprows=5）
    try:
        data_df = pd.read_csv(fp, sep="\t", header=None, skiprows=5)
    except Exception:
        return None

    # m/z に対応する列の平均強度を計算
    feature_names = [f"mz_{mz:.4f}" for mz in mz_values]
    avg_dict = {}
    for fname, ci in zip(feature_names, col_indices):
        if ci < len(data_df.columns):
            avg_dict[fname] = [pd.to_numeric(data_df.iloc[:, ci], errors="coerce").mean()]

    if not avg_dict:
        return None
    return pd.DataFrame(avg_dict)


def find_tims_file_path(data_folder: str, stem: str) -> Optional[str]:
    """ファイル名（拡張子なし）からフルパスを解決する。"""
    folder = Path(data_folder)
    if not folder.is_dir():
        return None
    extensions = [".parquet", ".pq", ".csv", ".tsv", ".txt"]
    for ext in extensions:
        fp = folder / f"{stem}{ext}"
        if fp.exists():
            return str(fp)
    return None


def read_parquet_annotations(file_path: str) -> list[str]:
    """Parquetファイルの annotation 列からユニーク値を取得する。

    annotation 列がない場合は空リストを返す。

    ★ ver55.0: 領域アノテーション CSV を一枚も渡していない変換では、annotation 列は
    全行 'Unannotated' になる（それ以前は Spot ファイル名由来のラベルが入っていた）。
    どちらも「領域の指定は無い」状態なので、選択肢としては出さない。
    フッタの `annotation_source` が 'none' ならその場で空を返し、無い旧ファイルでも
    ラベルが 1 種類だけならユーザーが領域を分けていないと判断できる。
    """
    try:
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(file_path)
        if "annotation" not in pf.schema.names:
            return []
        md = pf.schema_arrow.metadata or {}
        if md.get(b"annotation_source") == b"none":
            return []
        df = pd.read_parquet(file_path, columns=["annotation"])
        unique = df["annotation"].dropna().unique().tolist()
        labels = sorted(set(str(a).strip() for a in unique if str(a).strip()))
        # 旧ファイル（annotation_source を持たない）向けの後方互換判定。
        if b"annotation_source" not in md and len(labels) <= 1:
            return []
        return labels
    except Exception:
        return []


def _looks_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def read_desi_roi_list(file_path: str, *, max_roi: int = 200) -> list[str]:
    """DESI .txt の「ROI 列（領域ラベル）」のユニーク値一覧を返す。

    末尾の line/pixel などのピクセル連番（数値）列を ROI と誤認しないよう、列の中身で
    判定する。データ列 (index>=3) を右から走査し、
      「非空値の過半が非数値の文字列ラベルで、ユニーク数が小さく (<= max_roi) 行数未満」
    を満たす列を ROI 列として採用し、そのユニーク値を返す。
    該当列が無い / 検出失敗時は空リストを返す（= ROI 無し）。
    （列番号(n_metabolite)推定に依存しないため、ヘッダのズレにも強い。）
    """
    # `.txt` が無ければ、同 stem の Excel/CSV から正規 .txt へ自動変換して読む。
    if not Path(file_path).is_file():
        from app.services import desi_converter
        _p = Path(file_path)
        _converted = desi_converter.ensure_desi_txt(_p.stem, _p.parent)
        if _converted is None:
            return []
        file_path = str(_converted)
    # ★ ver55.2: ヘッダは 4 行固定ではない (実データは 5 行: 空/化合物名/代謝物番号/Q1/Q3)。
    #   4 行決め打ちだと Q3 の行をデータ行として ROI 判定の集計に混ぜてしまう。
    from app.services.desi_header import read_desi_header
    _hdr = read_desi_header(file_path)
    n_header = _hdr.n_header if _hdr is not None else 4
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(n_header):  # ヘッダ行スキップ (R fread skip=n_header と整合)
                if not f.readline():
                    return []

            n_rows = 0
            max_cols = 0
            # 列ごとに {uniq(set, max_roi 超で None=多すぎ), total, nonnum} を集計（メモリ上限つき）
            cols: dict[int, dict] = {}
            for raw_line in f:
                row = [c.strip() for c in raw_line.rstrip("\n").split("\t")]
                while row and not row[-1]:  # 末尾の空セルを除去
                    row.pop()
                if len(row) <= 3:
                    continue
                n_rows += 1
                if len(row) > max_cols:
                    max_cols = len(row)
                for ci in range(3, len(row)):
                    v = row[ci]
                    if not v:
                        continue
                    info = cols.get(ci)
                    if info is None:
                        info = {"uniq": set(), "total": 0, "nonnum": 0}
                        cols[ci] = info
                    info["total"] += 1
                    if not _looks_numeric(v):
                        info["nonnum"] += 1
                    u = info["uniq"]
                    if u is not None:
                        u.add(v)
                        if len(u) > max_roi:
                            info["uniq"] = None  # ユニーク多すぎ → ラベル列でない

            if n_rows == 0:
                return []

            # 末尾側の列を右から走査し、最初に見つかったラベル列を ROI とする
            for ci in range(max_cols - 1, 2, -1):
                info = cols.get(ci)
                if not info or info["total"] == 0 or info["uniq"] is None:
                    continue
                u = info["uniq"]
                if (info["nonnum"] > info["total"] * 0.5
                        and 0 < len(u) < n_rows):
                    return sorted(u)
            return []
    except Exception:
        return []


def validate_msi_file(file_path: str) -> dict:
    """MSIファイルの妥当性チェック"""
    path = Path(file_path)
    if not path.exists():
        return {"valid": False, "message": "ファイルが見つかりません"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [f.readline() for _ in range(12)]

        if len([l for l in lines if l]) < 5:
            return {"valid": False, "message": "ファイルの行数が不足しています (最低5行必要)"}

        # ★ ver55.2: 以前は「5 行目がタブ区切りで 4 列以上あるか」だけを見ていた。
        #   ヘッダ 5 行のファイルでは 5 行目が Q3 の行なので、この検査は
        #   **ヘッダをデータと誤認したまま通ってしまう**（実際に通っていた）。
        #   データ開始行を明示的に特定して検査する。
        from app.services.desi_header import is_data_line
        data_at = next((i for i, ln in enumerate(lines) if ln and is_data_line(ln)), None)
        if data_at is None:
            return {"valid": False, "message": "データ行が見つかりません (先頭のヘッダ形式が想定と異なります)"}
        if data_at < 1:
            return {"valid": False, "message": "ヘッダ行がありません"}
        if len(lines[data_at].strip().split("\t")) < 4:
            return {"valid": False, "message": "データ形式が正しくありません"}

        return {"valid": True, "message": "OK"}
    except Exception as e:
        return {"valid": False, "message": f"読み込みエラー: {e}"}


def load_mrm_file(mrm_path: str) -> Optional[pd.DataFrame]:
    """MRMファイルの読み込み (xlsx/csv)"""
    if not mrm_path or not Path(mrm_path).exists():
        return None

    ext = Path(mrm_path).suffix.lower()

    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(mrm_path)
        else:
            df = pd.read_csv(mrm_path)

        if df.empty:
            return None
        return df
    except Exception as e:
        logger.error("MRMファイル読み込みエラー: %s", e)
        return None


def list_result_folders(base_dir: str) -> list[dict]:
    """出力結果フォルダの一覧を取得"""
    base = Path(base_dir)
    if not base.is_dir():
        return []

    results = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue

        # RDS_Filesサブフォルダがあれば解析結果フォルダと判定
        has_rds = (d / "RDS_Files").is_dir()
        has_outputs = any(d.glob("*.png")) or any(d.glob("*.csv"))

        if has_rds or has_outputs:
            import re
            date_match = re.search(r"\d{8}", d.name)
            if date_match:
                ds = date_match.group()
                date_str = f"{ds[:4]}/{ds[4:6]}/{ds[6:8]}"
            else:
                date_str = "Unknown"

            results.append({
                "name": d.name,
                "path": str(d),
                "date": date_str,
            })

    return results



# ---------------------------------------------------------------------------
# C3: データ入力バリデーション
# ---------------------------------------------------------------------------

def validate_data_folder(folder_path: str, is_tims: bool = False) -> dict:
    """データフォルダの存在とファイル有無を検証する。

    Returns
    -------
    dict  {"ok": bool, "msg": str, "count": int}
    """
    if not folder_path or not folder_path.strip():
        return {"ok": False, "msg": "パスが未入力です", "count": 0}
    p = Path(folder_path)
    if not p.exists():
        return {"ok": False, "msg": "フォルダが見つかりません", "count": 0}
    if not p.is_dir():
        return {"ok": False, "msg": "指定パスはフォルダではありません", "count": 0}
    files = list_tims_files(folder_path) if is_tims else list_msi_files(folder_path)
    if not files:
        return {"ok": False, "msg": "データファイルが見つかりません", "count": 0}
    return {"ok": True, "msg": f"{len(files)} ファイル検出", "count": len(files)}


def validate_rds_folder(folder_path: str) -> dict:
    """RDSフォルダの存在と.rdsファイル有無を検証する。"""
    if not folder_path or not folder_path.strip():
        return {"ok": False, "msg": "パスが未入力です", "count": 0}
    p = Path(folder_path)
    if not p.exists():
        return {"ok": False, "msg": "フォルダが見つかりません", "count": 0}
    if not p.is_dir():
        return {"ok": False, "msg": "指定パスはフォルダではありません", "count": 0}
    rds = list(p.glob("*.rds"))
    if not rds:
        return {"ok": False, "msg": ".rds ファイルが見つかりません", "count": 0}
    return {"ok": True, "msg": f"{len(rds)} .rds ファイル検出", "count": len(rds)}


def validate_output_dir(folder_path: str) -> dict:
    """出力先フォルダの書き込み権限を検証する。"""
    if not folder_path or not folder_path.strip():
        return {"ok": False, "msg": "パスが未入力です"}
    p = Path(folder_path)
    # 親フォルダが存在すれば書き込みテスト
    target = p if p.is_dir() else p.parent
    if not target.is_dir():
        return {"ok": False, "msg": "親フォルダが見つかりません"}
    try:
        test_file = target / ".write_test_tmp"
        test_file.touch()
        test_file.unlink()
        return {"ok": True, "msg": "書き込み可能"}
    except OSError:
        return {"ok": False, "msg": f"書き込み権限がありません: {target}"}


def validate_numeric_param(value, name: str, min_val=None, max_val=None) -> dict:
    """数値パラメータの妥当性を検証する。"""
    if value is None or value == "":
        return {"ok": False, "msg": f"{name}: 値が未入力です"}
    try:
        v = float(value)
    except (ValueError, TypeError):
        return {"ok": False, "msg": f"{name}: 数値ではありません"}
    if min_val is not None and v < min_val:
        return {"ok": False, "msg": f"{name}: {min_val} 以上にしてください"}
    if max_val is not None and v > max_val:
        return {"ok": False, "msg": f"{name}: {max_val} 以下にしてください"}
    return {"ok": True, "msg": "OK"}
