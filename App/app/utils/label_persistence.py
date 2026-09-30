"""Label position persistence and interactive settings utilities.

Functions extracted from interactive_callbacks.py for reuse.
Handles reading/writing label_positions.json and interactive_settings.json,
annotation position extraction from relayout data, and Volcano annotation
offset computation.
"""

import json
import copy
import logging
import os
import re
import tempfile
import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from app.utils.file_locks import get_or_create_lock

logger = logging.getLogger(__name__)

# label_positions JSON の内容キャッシュ (ver46.1)。
# キーは (path, mtime_ns, size) なので、保存 (os.replace) されれば自動失効する。
_POSITIONS_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_POSITIONS_CACHE_MAX = int(os.environ.get("LABEL_POSITIONS_CACHE_MAX", 16))
_POSITIONS_CACHE_LOCK = threading.Lock()

# ★ ver76.0: 元クラスタとマージ表示の座標を共有すると、再配置が別図の手動位置も消す。
LABEL_SECTIONS = (
    "umap_integrated", "umap_integrated_merged",
    "umap_per_sample", "umap_per_sample_merged", "spatial", "spatial_merged",
)


def is_flat_label_section(section):
    return section in ("umap_integrated", "umap_integrated_merged")


def label_revision(positions):
    return int((positions or {}).get("_label_revision", 0))


def merge_position_sections(base, overlay):
    """既知の表示別座標だけをマージし、来歴メタデータを座標として扱わない。"""
    for section in LABEL_SECTIONS:
        updates = (overlay or {}).get(section)
        if not isinstance(updates, dict):
            continue
        saved = base.setdefault(section, {})
        if is_flat_label_section(section):
            merge_label_positions(saved, updates)
        else:
            for sample, positions in updates.items():
                if isinstance(positions, dict):
                    merge_label_positions(saved.setdefault(sample, {}), positions)
    return base


def clear_label_position_targets(positions, targets):
    """対象leafを丸ごと除去する。片方の軸や他の図の手動位置は混ぜない。"""
    result = copy.deepcopy(positions or {})
    for target in targets:
        section = target.get("section")
        if section not in LABEL_SECTIONS or target.get("kind") == "hne":
            continue
        saved = result.get(section, {})
        if not is_flat_label_section(section):
            saved = saved.get(str(target.get("sample", "")), {})
        for cluster in target.get("clusters", []):
            saved.pop(str(cluster), None)
    return result


def _load_for_update(path, rds_path, method):
    # ★ ver76.0: 旧共通JSONを丸ごと継承してから差分更新する。対象以外を消さない。
    source = path
    if not source.exists() and method:
        legacy = get_label_positions_path(rds_path)
        if legacy and legacy.exists():
            source = legacy
    if not source.exists():
        return {}
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ラベル位置ファイルの形式が不正です")
    return data


def reset_label_positions(targets, rds_path, method=None, *, expected_revision=None):
    """ロック内で対象だけ削除し、自動配置へ戻す。空の手法別JSONも保持する。"""
    path = get_label_positions_path(rds_path, method)
    if not path:
        raise ValueError("データが読み込まれていません")
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_or_create_lock(path):
        existing = _load_for_update(path, rds_path, method)
        revision = label_revision(existing)
        if expected_revision is not None and expected_revision != revision:
            raise ValueError("表示が更新されています。図を更新してから操作してください")
        result = clear_label_position_targets(existing, targets)
        result["_label_revision"] = revision + 1
        _atomic_write_json(path, result)
    return result


def _atomic_write_json(path: Path, data: dict) -> None:
    """JSON を原子的に書き込む（同ディレクトリに tmp 作成 → os.replace）"""
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=path.stem + "_",
    )
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


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_label_positions_path(rds_path: str | None, method: str | None = None) -> Path | None:
    """label_positions.json のパスを返す（RDSと同ディレクトリ）。

    method が指定された場合、手法別ファイル (label_positions_harmony.json 等) を返す。
    method=None の場合は従来の label_positions.json を返す（後方互換）。
    """
    if not rds_path:
        return None
    if method:
        safe_method = method.replace(" ", "_").lower()
        return Path(rds_path).parent / f"label_positions_{safe_method}.json"
    return Path(rds_path).parent / "label_positions.json"


def _read_positions_json(path: Path) -> dict | None:
    """label_positions JSON を (path, mtime, size) キーでキャッシュして読む (ver46.1)。

    この関数は Spatial / UMAP / Feature の描画コールバックから **1 回の再描画ごとに
    複数回**呼ばれる（ファセット表示では図の数だけ）。ファイル自体は
    ラベルをドラッグしたときにしか変わらないので、毎回 read_text + json.loads +
    logger.info を実行するのは無駄だった。

    書き込みは save_label_positions が os.replace で原子的に行うため、
    更新されれば mtime/size が変わりキャッシュは自動的に無効化される。
    失敗時 None（呼び出し元は「読めなかった」として従来どおり処理する）。

    ★ ver51.9 / C-1: **複製を返す**。従来はキャッシュの dict をそのまま返して
      いたが、描画側 (`interactive_umap._get_merged_label_positions`) は
      返ってきた dict と入れ子を **in-place で merge** する。本番は 1 プロセスで
      キャッシュはプロセス共有なので、あるセッションのドラッグ途中の位置が
      キャッシュへ焼き込まれ、別セッションの描画と PPTX に混ざっていた。
      ファイルは変わらないので mtime キーは有効なまま = **再起動するまで消えない**。

      呼び出し側の in-place merge はそのままにする。そちらを直すには
      4 つの描画経路すべてを揃える必要があり、1 つでも漏らすと同じことが起きる。
      ここで断てば経路が増えても安全。
    """
    import copy

    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    with _POSITIONS_CACHE_LOCK:
        hit = _POSITIONS_CACHE.get(key)
        if hit is not None:
            _POSITIONS_CACHE.move_to_end(key)
            return copy.deepcopy(hit)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[label_persistence] load failed: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    with _POSITIONS_CACHE_LOCK:
        _POSITIONS_CACHE[key] = data
        _POSITIONS_CACHE.move_to_end(key)
        while len(_POSITIONS_CACHE) > _POSITIONS_CACHE_MAX:
            _POSITIONS_CACHE.popitem(last=False)
    # 初回も複製を返す（呼び出し側は自分が 1 回目かどうかを知らない）
    return copy.deepcopy(data)


def load_label_positions(rds_path: str | None, method: str | None = None) -> dict:
    """label_positions.json を読み込んで dict を返す。ファイルなし or エラー時は空dict。

    method 指定時はまず手法別ファイルを探し、なければ旧共有ファイルにフォールバック。
    """
    path = get_label_positions_path(rds_path, method)
    if path and path.exists():
        data = _read_positions_json(path)
        if data is not None:
            logger.debug(
                "[label_persistence] loaded: path=%s sections=%s",
                path.name, list(data.keys()),
            )
            return data
        return {}
    # フォールバック: 手法別ファイルがない場合、旧共有 label_positions.json を読む
    if method:
        legacy = get_label_positions_path(rds_path, None)
        if legacy and legacy.exists():
            data = _read_positions_json(legacy)
            if data is not None:
                logger.debug(
                    "[label_persistence] loaded (legacy fallback): path=%s sections=%s",
                    legacy.name, list(data.keys()),
                )
                return data
    logger.debug(
        "[label_persistence] no file found: rds_path=%s method=%s",
        rds_path, method,
    )
    return {}


# ---------------------------------------------------------------------------
# Interactive settings
# ---------------------------------------------------------------------------

def get_interactive_settings_path(rds_path: str | None) -> Path | None:
    """interactive_settings.json のパスを返す（RDSと同ディレクトリ）"""
    if not rds_path:
        return None
    return Path(rds_path).parent / "interactive_settings.json"


def load_interactive_settings(rds_path: str | None) -> dict:
    """interactive_settings.json を読み込み。ファイルなし/エラー時は空dict"""
    path = get_interactive_settings_path(rds_path)
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_interactive_settings(key: str, value, rds_path: str | None) -> None:
    """interactive_settings.json の指定キーを filelock + 原子的に書き込む。

    同一プロジェクトを複数タブで開いた際のロストアップデート防止のため、
    ロック内で「読込→改変→atomic write」を一貫して実行する。
    """
    path = get_interactive_settings_path(rds_path)
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = get_or_create_lock(path)
        with lock:
            existing = {}
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            existing[key] = value
            existing["_saved_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            _atomic_write_json(path, existing)
    except Exception as e:
        logger.warning("インタラクティブ設定の保存に失敗: %s", e)


def cluster_name_map_key(method) -> str:
    """クラスタ名変更マップの保存キー。手法(Harmony/RPCA 等)ごとに独立させる。

    手法が未指定/不明のときは旧来の共有キー 'cluster_name_map' を使う。
    """
    m = str(method).strip() if method else ""
    return f"cluster_name_map::{m}" if m else "cluster_name_map"


def load_cluster_name_map(rds_path: str | None, method=None) -> dict:
    """手法別クラスタ名変更マップを読み込む。

    手法別キーが無ければ旧来の共有キー 'cluster_name_map' にフォールバックする
    （既存のリネームを失わないため）。
    """
    settings = load_interactive_settings(rds_path) or {}
    key = cluster_name_map_key(method)
    nm = settings.get(key)
    if nm is None and key != "cluster_name_map":
        nm = settings.get("cluster_name_map")  # 旧形式（手法共有）フォールバック
    return nm or {}


def save_cluster_name_map(rds_path: str | None, method, value) -> None:
    """手法別クラスタ名変更マップを保存する。"""
    save_interactive_settings(cluster_name_map_key(method), value, rds_path)


def save_label_positions(
    positions: dict,
    rds_path: str | None,
    method: str | None = None,
    merge: bool = True,
    *, expected_revision: int | None = None, strict: bool = False,
) -> bool:
    """label_positions.json を filelock + 原子的に書き込む。

    Args:
        positions: 保存する辞書。merge=False なら全置換。
        rds_path: RDSパス（保存先決定用）
        method: 統合手法（None なら共有ファイル、指定時は手法別ファイル）
        merge: True ならファイル内既存エントリにマージ、False なら全置換

    Note:
        merge=True の場合、各セクション (umap_integrated / spatial_<sample> 等) に
        対して既存値と新規値を merge_label_positions でディープマージする。
    """
    path = get_label_positions_path(rds_path, method)
    if not path:
        logger.info(
            "[label_persistence] save skipped (no path): rds_path=%s method=%s",
            rds_path, method,
        )
        return False
    logger.info(
        "[label_persistence] saving: path=%s sections=%s merge=%s",
        path.name, list((positions or {}).keys()), merge,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = get_or_create_lock(path)
        with lock:
            if merge:
                existing = _load_for_update(path, rds_path, method)
                # ★ ver76.0: 再配置以前のドラッグ/DOMスナップショットによる復活を防ぐ。
                if (expected_revision is not None
                        and label_revision(existing) != expected_revision):
                    return False
                merge_position_sections(existing, positions)
                _atomic_write_json(path, existing)
            else:
                _atomic_write_json(path, positions or {})
        return True
    except Exception as e:
        if strict:
            raise
        logger.warning("ラベル位置の保存に失敗: %s", e)
        return False


# ---------------------------------------------------------------------------
# Annotation position helpers
# ---------------------------------------------------------------------------

def extract_annotation_positions_by_name(
    relayout_data: dict | None,
    clusters: list,
) -> dict:
    """relayoutData の annotations[N].x/y をクラスタ名ベースで抽出。

    Parameters
    ----------
    relayout_data : dict  -- Dash relayoutData
    clusters : list       -- ソート済みクラスタ名リスト（annotation 追加順と一致）

    Returns
    -------
    dict -- {"クラスタ名": {"x": v, "y": v}}  （更新があったもののみ）
    """
    if not relayout_data or not clusters:
        return {}
    positions = {}
    sorted_clusters = list(clusters)
    for key, val in relayout_data.items():
        m = re.match(r"annotations\[(\d+)\]\.([xy])", key)
        if m:
            idx = int(m.group(1))
            attr = m.group(2)
            if idx < len(sorted_clusters):
                cl = str(sorted_clusters[idx])
                if cl not in positions:
                    positions[cl] = {}
                positions[cl][attr] = val
    return positions


def merge_label_positions(base: dict, overlay: dict | None) -> dict:
    """base dict に overlay dict をディープマージ（overlay が優先）。

    base/overlay は {"key": {"x": v, "y": v}} 形式。
    base を破壊的に更新して返す。
    """
    for k, v in (overlay or {}).items():
        if k not in base:
            base[k] = {}
        if isinstance(v, dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def compute_annotation_offsets(
    points: list[tuple],
    font_size: int = 9,
    base_offset: int = 25,
) -> list[tuple[float, float]]:
    """Volcanoアノテーション位置を計算し、重複を回避する。

    Args:
        points: [(x, y, text), ...]
        font_size: フォントサイズ（近似幅計算用）
        base_offset: 矢印の基本長さ（ピクセル）

    Returns:
        [(ax, ay), ...] -- 各ポイントのオフセット
    """
    placed = []  # [(cx, cy, w, h), ...] -- 配置済みアノテーションの中心とサイズ
    offsets = []
    # 角度候補: 上→右上→左上→右→左→下 の順に試行
    angle_candidates = [
        (0, -1),    # 上
        (1, -1),    # 右上
        (-1, -1),   # 左上
        (1, 0),     # 右
        (-1, 0),    # 左
        (0, 1),     # 下
        (1, 1),     # 右下
        (-1, 1),    # 左下
    ]
    for x, y, text in points:
        text_str = str(text).replace("\n", " ")
        est_w = len(text_str) * font_size * 0.6
        est_h = font_size * 1.5
        # 複数行の場合は高さを増やす
        n_lines = str(text).count("\n") + 1
        est_h *= n_lines

        best_ax, best_ay = 0, -base_offset
        best_overlap = float("inf")
        for dx, dy in angle_candidates:
            for dist_mult in [1.0, 1.5, 2.0]:
                ax = dx * base_offset * dist_mult
                ay = dy * base_offset * dist_mult
                cx = x + ax
                cy = y + ay
                overlap_count = 0
                for px, py, pw, ph in placed:
                    if (abs(cx - px) < (est_w + pw) / 2 and
                            abs(cy - py) < (est_h + ph) / 2):
                        overlap_count += 1
                if overlap_count < best_overlap:
                    best_overlap = overlap_count
                    best_ax, best_ay = ax, ay
                if overlap_count == 0:
                    break
            if best_overlap == 0:
                break
        placed.append((x + best_ax, y + best_ay, est_w, est_h))
        offsets.append((best_ax, best_ay))
    return offsets
