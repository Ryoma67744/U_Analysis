"""全切片の登録情報完全性を、UI・変換・解析で共通検証する。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


REQUIRED_SECTION_FIELDS = (
    "section_display_name",
    "subject_id",
    "group",
    "metadata_confirmed",
)
EXPLICIT_NOT_APPLICABLE = "該当なし"


@dataclass(frozen=True)
class SectionMetadataIssue:
    section_id: str
    display_name: str
    missing_fields: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict:
        value = asdict(self)
        value["missing_fields"] = list(self.missing_fields)
        return value


def _text(value) -> str:
    return str(value or "").strip()


def is_metadata_confirmed(value) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return _text(value).lower() in {"true", "1", "yes", "確認済"}


def _has_control_characters(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def normalized_registered_sections(sections: Iterable[dict] | None) -> list[dict]:
    """解析選択に依存しない登録用section表現を返す。"""
    result: list[dict] = []
    for raw in sections or []:
        row = deepcopy(raw)
        row["section_id"] = _text(row.get("section_id"))
        row["section_display_name"] = _text(row.get("section_display_name"))
        row["subject_id"] = _text(row.get("subject_id"))
        row["group"] = _text(row.get("group"))
        row["metadata_confirmed"] = is_metadata_confirmed(row.get("metadata_confirmed", False))
        row["component_ids"] = sorted({str(value) for value in row.get("component_ids", []) if str(value)})
        row["pixel_count"] = int(row.get("pixel_count", 0))
        # selected は解析条件であり、登録内容・登録hashへ混入させない。
        row.pop("selected", None)
        row.pop("integration_unit_id", None)
        row.pop("roi", None)
        row.pop("annotation_label", None)
        result.append(row)
    return sorted(result, key=lambda row: (row["section_display_name"], row["section_id"]))


def validate_registered_sections(
    sections: Iterable[dict] | None,
    *,
    components: dict[str, int] | None = None,
    require_confirmation: bool = True,
) -> list[SectionMetadataIssue]:
    rows = normalized_registered_sections(sections)
    issues: list[SectionMetadataIssue] = []
    if not rows:
        return [SectionMetadataIssue("", "（切片なし）", reason="登録切片がありません")]

    seen_ids: set[str] = set()
    names: dict[str, list[str]] = {}
    component_owner: dict[str, str] = {}
    expected_components = {str(k): int(v) for k, v in (components or {}).items()}

    for row in rows:
        sid = row["section_id"]
        name = row["section_display_name"] or sid or "（名称未設定）"
        missing: list[str] = []
        if not sid:
            missing.append("section_id")
        if not row["section_display_name"]:
            missing.append("切片名")
        if not row["subject_id"]:
            missing.append("個体／独立試料ID")
        if not row["group"]:
            missing.append("群")
        for field_label, field_value in (
            ("切片名", row["section_display_name"]),
            ("個体／独立試料ID", row["subject_id"]),
            ("群", row["group"]),
        ):
            if field_value and _has_control_characters(field_value):
                issues.append(SectionMetadataIssue(
                    sid, name, reason=f"{field_label}に制御文字を使用できません",
                ))
        if require_confirmation and not row["metadata_confirmed"]:
            missing.append("登録確認")
        if expected_components and not row["component_ids"]:
            missing.append("座標component")
        if missing:
            issues.append(SectionMetadataIssue(sid, name, tuple(missing)))

        if sid:
            if sid in seen_ids:
                issues.append(SectionMetadataIssue(sid, name, reason="section_idが重複しています"))
            seen_ids.add(sid)
        if row["section_display_name"]:
            names.setdefault(row["section_display_name"], []).append(sid)

        expected_pixels = 0
        for component_id in row["component_ids"]:
            if expected_components and component_id not in expected_components:
                issues.append(SectionMetadataIssue(sid, name, reason=f"未知の座標component: {component_id}"))
                continue
            if component_id in component_owner:
                issues.append(SectionMetadataIssue(
                    sid, name,
                    reason=(f"座標component {component_id} が複数切片に所属しています"
                            f"（{component_owner[component_id]} / {sid}）"),
                ))
            component_owner[component_id] = sid
            expected_pixels += expected_components.get(component_id, 0)
        if expected_components and row["pixel_count"] != expected_pixels:
            issues.append(SectionMetadataIssue(
                sid, name,
                reason=f"pixel数が一致しません（登録 {row['pixel_count']:,} / 座標 {expected_pixels:,}）",
            ))

    for name, ids in names.items():
        if len(ids) > 1:
            for sid in ids:
                issues.append(SectionMetadataIssue(sid, name, reason="切片名が重複しています"))

    if expected_components:
        missing_components = sorted(set(expected_components) - set(component_owner))
        for component_id in missing_components:
            issues.append(SectionMetadataIssue(
                "", component_id, reason="座標componentがどの切片にも登録されていません",
            ))

    # ★ ver74.0: 独立試料IDの群矛盾はUIだけでなく低レベル変換でも拒否する。
    subject_groups = {}
    for row in rows:
        subject = row["subject_id"]
        if subject and subject != EXPLICIT_NOT_APPLICABLE and row["group"]:
            subject_groups.setdefault(subject, set()).add(row["group"])
    for subject, groups in subject_groups.items():
        if len(groups) > 1:
            issues.append(SectionMetadataIssue("", subject, reason="同一個体／独立試料IDに複数の群があります"))

    # 同一内容の重複表示を避ける。
    unique: list[SectionMetadataIssue] = []
    seen = set()
    for issue in issues:
        key = (issue.section_id, issue.display_name, issue.missing_fields, issue.reason)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def format_section_issues(issues: Iterable[SectionMetadataIssue], *, filename: str = "") -> str:
    prefix = f"{Path(filename).name}: " if filename else ""
    lines = [
        prefix + "解析対象外を含む全切片の必須情報を登録してください。",
        "今回の解析に使用しない切片も検査対象です。",
    ]
    for issue in issues:
        label = issue.display_name or issue.section_id or "（切片不明）"
        detail = "、".join(issue.missing_fields) if issue.missing_fields else issue.reason
        if issue.reason and issue.missing_fields:
            detail += f"／{issue.reason}"
        lines.append(f"・{label}: {detail}")
    return "\n".join(lines)


def require_complete_sections(
    sections: Iterable[dict] | None,
    *,
    components: dict[str, int] | None = None,
    filename: str = "",
    require_confirmation: bool = True,
) -> list[dict]:
    issues = validate_registered_sections(
        sections,
        components=components,
        require_confirmation=require_confirmation,
    )
    if issues:
        # 遅延importで循環参照を避ける。
        from app.services.input_preparation import InputPreparationError
        raise InputPreparationError(format_section_issues(issues, filename=filename))
    return normalized_registered_sections(sections)
