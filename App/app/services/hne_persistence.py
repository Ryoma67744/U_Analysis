# =============================================================================
# MSI Analysis Application - 解剖×クラスタ（H&E オーバーレイ）個体別 永続化
# =============================================================================
# H&E オーバーレイの個体（Sample）別状態（ROIポリゴン・対応点・回転・H&E画像）を
# RDS と同じフォルダに保存/復元する。label_persistence と同じ流儀
# （<RDS-dir>/<file> へ atomic 書き込み＋FileLock）。画像は重いので JSON 非格納で
# PNG を <RDS-dir>/hne_overlay/ に保存し、JSON にはファイル名のみ持たせる。
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from app.utils.file_locks import get_or_create_lock

logger = logging.getLogger("msi.hne_persistence")


def _atomic_write_json(path: Path, data: dict) -> None:
    """JSON を原子的に書き込む（同ディレクトリに tmp 作成 → os.replace）。"""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp",
                                    prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def hne_state_path(rds_path):
    """hne_overlay_state.json のパス（RDSと同ディレクトリ）。"""
    if not rds_path:
        return None
    return Path(rds_path).parent / "hne_overlay_state.json"


def hne_image_dir(rds_path):
    """個体別 H&E PNG 保存ディレクトリ（<RDS-dir>/hne_overlay/）。"""
    if not rds_path:
        return None
    return Path(rds_path).parent / "hne_overlay"


def _safe(name) -> str:
    """ファイル名に使える安全な文字列へ（個体名の "/" 等を "_" に）。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name or "sample"))


def load_hne_overlay(rds_path) -> dict:
    """個体別状態マップ {sample: entry, ...} を読み込む。無ければ空dict。"""
    path = hne_state_path(rds_path)
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("H&E 状態の読込に失敗: %s", e)
    return {}


def load_hne_sample(rds_path, sample) -> dict:
    """指定個体の entry を返す（無ければ空dict）。"""
    if not sample:
        return {}
    return (load_hne_overlay(rds_path) or {}).get(str(sample), {}) or {}


def save_hne_overlay_sample(rds_path, sample, partial: dict) -> None:
    """個体 entry に partial をマージ保存（他個体・他キーは保持）。atomic＋FileLock。"""
    path = hne_state_path(rds_path)
    if not path or not sample:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = get_or_create_lock(path)
        with lock:
            data = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            entry = dict(data.get(str(sample), {}))
            entry.update(partial or {})
            data[str(sample)] = entry
            data["_saved_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            _atomic_write_json(path, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 状態の保存に失敗: %s", e)


def save_hne_image(rds_path, sample, data_uri):
    """base64 data URI の H&E 画像を PNG 保存し、ファイル名を返す（失敗時 None）。"""
    d = hne_image_dir(rds_path)
    if not d or not sample or not data_uri or "," not in str(data_uri):
        return None
    try:
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{_safe(sample)}.png"
        (d / fname).write_bytes(base64.b64decode(str(data_uri).split(",", 1)[1]))
        return fname
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 画像の保存に失敗: %s", e)
        return None


def load_hne_image_b64(rds_path, filename):
    """保存済み PNG を data URI 文字列に戻す（無ければ None）。"""
    d = hne_image_dir(rds_path)
    if not d or not filename:
        return None
    p = d / filename
    if not p.exists():
        return None
    try:
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception as e:  # noqa: BLE001
        logger.warning("H&E 画像の読込に失敗: %s", e)
        return None


def save_metaboanalyst_csv(rds_path, filename, df):
    """MetaboAnalyst 用 CSV を `<RDS隣>/metaboanalyst_exports/<filename>` に atomic 保存し、
    保存先パス（str）を返す。失敗時 None。

    ブラウザのダウンロードが届かない/陳腐化した環境でも、サーバ上の確定パスから
    結果を取得できるようにするための保険（dcc.Download と二重化）。
    """
    if not rds_path or not filename:
        return None
    try:
        out_dir = Path(rds_path).parent / "metaboanalyst_exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        fd, tmp_path = tempfile.mkstemp(dir=str(out_dir), suffix=".tmp",
                                        prefix=path.stem + "_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                df.to_csv(f, index=False)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return str(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("MetaboAnalyst CSV のサーバ保存に失敗: %s", e)
        return None


def save_metaboanalyst_bytes(rds_path, filename, data):
    """任意バイト列（ZIP バンドル等）を `<RDS隣>/metaboanalyst_exports/<filename>` に atomic 保存。

    保存先パス（str）を返す。失敗時 None。CSV 版と同じディレクトリ規約。
    """
    if not rds_path or not filename:
        return None
    try:
        out_dir = Path(rds_path).parent / "metaboanalyst_exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        fd, tmp_path = tempfile.mkstemp(dir=str(out_dir), suffix=".tmp",
                                        prefix=path.stem + "_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return str(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("MetaboAnalyst バンドルのサーバ保存に失敗: %s", e)
        return None


def metaboanalyst_csv_path(rds_path, filename):
    """`save_metaboanalyst_csv` が書く CSV/バンドルのパス（Path）。無効時 None。"""
    if not rds_path or not filename:
        return None
    return Path(rds_path).parent / "metaboanalyst_exports" / filename


def _export_cache_key_path(rds_path, filename):
    if not rds_path or not filename:
        return None
    return (Path(rds_path).parent / "metaboanalyst_exports"
            / (str(filename) + ".cachekey.json"))


def save_export_cache_key(rds_path, filename, key):
    """エクスポート結果CSVのキャッシュキーを保存（2回目以降の即時化用）。"""
    p = _export_cache_key_path(rds_path, filename)
    if not p:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(p, {"key": str(key),
                               "saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")})
    except Exception as e:  # noqa: BLE001
        logger.warning("エクスポートキャッシュキーの保存に失敗: %s", e)


def load_export_cache_key(rds_path, filename):
    """保存済みキャッシュキー（str）を返す。無ければ None。"""
    p = _export_cache_key_path(rds_path, filename)
    if not p or not p.exists():
        return None
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("key")
    except Exception as e:  # noqa: BLE001
        logger.warning("エクスポートキャッシュキーの読込に失敗: %s", e)
        return None
