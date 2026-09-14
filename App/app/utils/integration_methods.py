"""解析手法の表示名と、既存結果を参照する内部名の対応。"""

from collections.abc import Iterable, Mapping
from pathlib import Path


UNCORRECTED_PCA = "PCA (uncorrected)"
_AUXILIARY_PREFIXES = ("umap_", "deg_", "plotdata_", "feature_", "pixel_table_")


def is_auxiliary_rds(path: str | Path) -> bool:
    """Seurat 主結果ではない、描画・集計用 RDS を除外する。"""
    return Path(path).name.lower().startswith(_AUXILIARY_PREFIXES)


def method_display_name(method: str | None) -> str:
    """★ ver66.4: 表示だけを PCA に統一し、DEG・保存設定の内部名を保つ。"""
    name = str(method or "")
    return "PCA" if name.casefold() == UNCORRECTED_PCA.casefold() else name


def method_options(methods: Iterable[str]) -> list[dict[str, str]]:
    """補正前の専用結果があれば、それを唯一の PCA 選択肢にする。"""
    keys = list(dict.fromkeys(methods))
    if UNCORRECTED_PCA in keys:
        keys = [key for key in keys if key != "PCA"]
    return [{"label": method_display_name(key), "value": key} for key in keys]


def default_viewer_method(methods: Iterable[str]) -> str | None:
    """通常ビューアーと全手法共有で、利用可能な初期結果を同じ順に選ぶ。"""
    # ★ ver66.5: Harmony 固定では希望する RPCA 結果から閲覧を始められない。
    # 検出順に依存させず RPCA、Harmony、PCA の順で選び、補正なし内部名を保つ。
    options = method_options(methods)
    available = {option["value"] for option in options}
    for method in ("RPCA", "Harmony", UNCORRECTED_PCA, "PCA"):
        if method in available:
            return method
    return options[0]["value"] if options else None


def resolve_method_key(method: str | None, methods: Mapping | Iterable[str]) -> str | None:
    """表示名・旧共有名を実在する内部名へ解決する。別手法へは代替しない。"""
    keys = {str(key).casefold(): key for key in methods}
    requested = str(method or "").strip().casefold()
    # ★ ver66.4: 共有画面の旧固定値 PCA が専用結果を見つけられず、
    # 最初の RDS（多くは Harmony）へ落ちていたため、ここで対応付ける。
    if requested == "pca" and UNCORRECTED_PCA.casefold() in keys:
        return keys[UNCORRECTED_PCA.casefold()]
    return keys.get(requested)
