"""登録済みサイドカーの化合物名だけを出力する（Dash 非依存）。"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass

from app.services.annotation_inspect import (
    annotation_sidecar_identity,
    compound_names,
    load_annotation_sidecar,
)

logger = logging.getLogger(__name__)


class AnnotationExportError(ValueError):
    """利用者にそのまま表示できる、化合物名リスト出力のエラー。"""


@dataclass(frozen=True)
class CompoundNameExport:
    content: bytes
    filename: str
    count: int
    mime_type: str


def _filename(sub: dict, fmt: str, deduplicate: bool) -> str:
    name = str(sub.get("name") or sub.get("id") or "subproject")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name).strip(" .") or "subproject"
    # Windows は最初のドットより前を予約名と判定する（例: CON.txt）。
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]",
                    name.split(".", 1)[0].rstrip(), flags=re.IGNORECASE):
        name = "_" + name
    mode = "unique" if deduplicate else "all"
    suffix = f"_compound_names_{mode}.{fmt}"
    # 文字数ではなく UTF-8 バイト数で制限し、日本語名も一般的な255-byte上限に収める。
    budget = 200 - len(suffix.encode("utf-8"))
    name = name.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .")
    return f"{name or 'subproject'}{suffix}"


def export_compound_names(sub, expected_source, fmt="txt", deduplicate=True) -> CompoundNameExport:
    """プレビューと同じ参照元・版の全有効名を出力する。

    expected_source は比較にのみ用い、そこからファイルパスを取得しない。
    データの変化を検出した場合は、別のファイルへ自動的に切り替えない。
    """
    if fmt not in ("txt", "csv"):
        raise AnnotationExportError("出力形式は TXT または CSV を選択してください。")
    if not isinstance(deduplicate, bool):
        raise AnnotationExportError("重複を除く設定を確認してください。")
    if not isinstance(sub, dict) or not isinstance(expected_source, dict) or not expected_source:
        raise AnnotationExportError("画面を閉じて再度開いてからダウンロードしてください。")

    try:
        current = annotation_sidecar_identity(sub)
        if current is None:
            raise AnnotationExportError(
                "化合物名一覧を出力できません。サイドカーのある TIMS/SCiLS データが対象です。")
        if current != expected_source:
            raise AnnotationExportError("参照元が更新されました。画面を閉じて再度開いてください。")
        frame, _path, source = load_annotation_sidecar(sub)
        if source != expected_source:
            raise AnnotationExportError("参照元が更新されました。画面を閉じて再度開いてください。")
        names = compound_names(frame, deduplicate=deduplicate)
        if not names:
            raise AnnotationExportError("出力できる化合物名がありません。")
        if fmt == "txt":
            if any("\r" in name or "\n" in name for name in names):
                raise AnnotationExportError("改行を含む化合物名があります。CSV 形式を選択してください。")
            content = ("\n".join(names) + "\n").encode("utf-8")
            mime_type = "text/plain;charset=utf-8"
        else:
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\r\n")
            writer.writerow(["compound"])
            writer.writerows((name,) for name in names)
            content = buffer.getvalue().encode("utf-8-sig")
            mime_type = "text/csv;charset=utf-8"
        if annotation_sidecar_identity(sub) != source:
            raise AnnotationExportError("参照元が更新されました。画面を閉じて再度開いてください。")
    except AnnotationExportError:
        raise
    except Exception as exc:
        logger.warning("化合物名リストの読取に失敗", exc_info=True)
        raise AnnotationExportError(
            "化合物名一覧を読み取れませんでした。参照元を確認し、画面を閉じて再度開いてください。"
        ) from exc
    return CompoundNameExport(content, _filename(sub, fmt, deduplicate), len(names), mime_type)
