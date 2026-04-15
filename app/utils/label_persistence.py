"""Label position persistence and interactive settings utilities.

Functions extracted from interactive_callbacks.py for reuse.
Handles reading/writing label_positions.json and interactive_settings.json,
annotation position extraction from relayout data, and Volcano annotation
offset computation.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


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


def load_label_positions(rds_path: str | None, method: str | None = None) -> dict:
    """label_positions.json を読み込んで dict を返す。ファイルなし or エラー時は空dict。

    method 指定時はまず手法別ファイルを探し、なければ旧共有ファイルにフォールバック。
    """
    path = get_label_positions_path(rds_path, method)
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    # フォールバック: 手法別ファイルがない場合、旧共有 label_positions.json を読む
    if method:
        legacy = get_label_positions_path(rds_path, None)
        if legacy and legacy.exists():
            try:
                return json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                pass
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
    """interactive_settings.json の指定キーを更新して書き込む"""
    path = get_interactive_settings_path(rds_path)
    if not path:
        return
    try:
        existing = load_interactive_settings(rds_path)
        existing[key] = value
        existing["_saved_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("インタラクティブ設定の保存に失敗: %s", e)


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
