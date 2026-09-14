"""PCA の共有が補正なし RDS を指し、別手法へすり替わらないこと。"""

from types import SimpleNamespace

import pytest
from dash import no_update

from app.callbacks import interactive_callbacks as IC
from app.callbacks import project_callbacks as PC


@pytest.fixture
def share_context(monkeypatch):
    """共有台帳・抽出処理には触れず、保存引数と事前抽出対象を記録する。"""
    context = SimpleNamespace(
        rds_map={
            "Harmony": "/results/Step2_HarmonyPCA_Result.rds",
            "RPCA": "/results/Step3_RPCA_Result.rds",
            "PCA (uncorrected)": "/results/Step2_PCA_uncorrected.rds",
        },
        sub={"name": "sub", "last_result_dir": "/results"},
        created=[],
        warmed=[],
    )
    monkeypatch.setattr(PC, "get_project", lambda project_id: {"name": "project"})
    monkeypatch.setattr(PC, "get_sub_project", lambda *args: context.sub)
    monkeypatch.setattr(IC, "_detect_integration_methods", lambda path: context.rds_map)
    monkeypatch.setattr(PC, "Path", lambda path: SimpleNamespace(exists=lambda: True))

    def record_share(kind):
        def create(**kwargs):
            context.created.append({"kind": kind, **kwargs})
            return {"token": "test-token"}
        return create

    monkeypatch.setattr(PC, "create_share", record_share("expiring"))
    monkeypatch.setattr(PC, "create_persistent_share", record_share("persistent"))
    monkeypatch.setattr(PC, "build_share_url", lambda token: "/share/" + token)
    monkeypatch.setattr(PC, "build_persistent_view_url", lambda token: "/view/" + token)
    monkeypatch.setattr(PC, "_render_share_links", lambda project_id: "share-links")

    def thread(*, target, args, daemon):
        assert target is PC._prewarm_share_cache
        assert daemon is True
        return SimpleNamespace(start=lambda: context.warmed.append(args[0]))

    monkeypatch.setattr(PC.threading, "Thread", thread)
    return context


def _generate(method, kind="expiring"):
    return PC.generate_share_link(
        1, "sub-id", {"id": "project-id"}, kind, "30", method, True, "memo",
    )


@pytest.mark.parametrize("kind", ["expiring", "persistent"])
@pytest.mark.parametrize("method", ["PCA", "PCA (uncorrected)"])
def test_pca_share_persists_exact_uncorrected_result(share_context, method, kind):
    """表示名と過去の内部名のいずれでも、RDS と保存手法が一致する。"""
    out = _generate(method, kind)

    assert len(share_context.created) == 1
    saved = share_context.created[0]
    assert saved["kind"] == kind
    assert saved["integration_method"] == "PCA (uncorrected)"
    assert saved["rds_path"] == "/results/Step2_PCA_uncorrected.rds"
    assert saved["require_password"] is True
    assert saved["memo"] == "memo"
    assert share_context.warmed == [saved["rds_path"]]
    assert out[:3] == ({}, "/view/test-token" if kind == "persistent"
                      else "/share/test-token", "share-links")


def test_legacy_standalone_pca_can_still_be_shared(share_context):
    """補正なしの補助出力を持たない過去の単独 PCA 結果は利用できる。"""
    share_context.rds_map = {"PCA": "/results/Step2_PCA_Result.rds"}
    _generate("PCA")
    saved = share_context.created[0]
    assert saved["integration_method"] == "PCA"
    assert saved["rds_path"] == "/results/Step2_PCA_Result.rds"


@pytest.mark.parametrize("kind", ["expiring", "persistent"])
def test_missing_pca_does_not_create_a_harmony_share(share_context, kind):
    """PCA が無い場合、先頭にある Harmony を共有しない。"""
    share_context.rds_map.pop("PCA (uncorrected)")
    out = _generate("PCA", kind)
    assert share_context.created == []
    assert share_context.warmed == []
    assert out[0] == {"display": "none"}
    assert out[1] == ""
    assert out[2] is no_update
    assert out[3] is True
    assert "PCA の解析結果が見つかりません" in out[4]
    assert out[5] == "danger"


@pytest.mark.parametrize("missing", ["folder", "results", "path"])
def test_unavailable_result_does_not_create_an_empty_pca_share(share_context, missing):
    """結果フォルダ未設定・RDS 未検出・空パスでも共有を作らない。"""
    if missing == "folder":
        share_context.sub = {"name": "sub"}
    elif missing == "results":
        share_context.rds_map = {}
    else:
        share_context.rds_map["PCA (uncorrected)"] = ""
    out = _generate("PCA")
    assert share_context.created == []
    assert out[3] is True
    assert out[5] == "danger"


@pytest.mark.parametrize("kind", ["expiring", "persistent"])
def test_all_methods_sharing_keeps_all_and_rpca_default(share_context, kind):
    """全手法共有では all を保存し、既定の RPCA で事前抽出する。"""
    _generate("all", kind)
    saved = share_context.created[0]
    assert saved["integration_method"] == "all"
    assert saved["rds_path"] == "/results/Step3_RPCA_Result.rds"
    assert share_context.warmed == [saved["rds_path"]]


@pytest.mark.parametrize("kind", ["expiring", "persistent"])
@pytest.mark.parametrize("methods, expected", [
    (["PCA (uncorrected)", "Harmony"], "Harmony"),
    (["PCA (uncorrected)"], "PCA (uncorrected)"),
    (["PCA"], "PCA"),
])
def test_all_methods_sharing_without_rpca_uses_available_default(share_context, kind, methods, expected):
    """RPCA が無い共有は利用可能な Harmony または PCA を先読みする。"""
    share_context.rds_map = {method: f"/results/{index}.rds" for index, method in enumerate(methods)}
    _generate("all", kind)
    saved = share_context.created[0]
    assert saved["integration_method"] == "all"
    assert saved["rds_path"] == share_context.rds_map[expected]
    assert share_context.warmed == [saved["rds_path"]]


def test_all_methods_sharing_uses_visible_pca_when_both_pcas_exist(share_context):
    """全手法共有の初期結果も、画面で選べる補正なし PCA と一致する。"""
    share_context.rds_map = {
        "PCA": "/results/Step2_PCA_Result.rds",
        "PCA (uncorrected)": "/results/Step2_PCA_uncorrected.rds",
    }
    _generate("all")
    saved = share_context.created[0]
    assert saved["integration_method"] == "all"
    assert saved["rds_path"] == "/results/Step2_PCA_uncorrected.rds"
    assert share_context.warmed == [saved["rds_path"]]


@pytest.mark.parametrize("method", ["Harmony", "RPCA"])
def test_other_named_methods_keep_their_exact_result(share_context, method):
    _generate(method)
    saved = share_context.created[0]
    assert saved["integration_method"] == method
    assert saved["rds_path"] == share_context.rds_map[method]
    assert share_context.warmed == [saved["rds_path"]]


@pytest.mark.parametrize("kind", ["expiring", "persistent"])
def test_saved_share_list_displays_pca_without_changing_record(share_context, kind):
    record = {"token": "test-token", "integration_method": "PCA (uncorrected)"}
    row = PC._share_link_row(record, kind)
    info = row.children[0].children[-1].children
    assert info.startswith("統合: PCA | ")
    assert "uncorrected" not in info
    assert record["integration_method"] == "PCA (uncorrected)"
