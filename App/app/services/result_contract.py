"""結果の実内容と利用可能な段階を表す、Dash 非依存の契約。"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

DESCRIPTOR_VERSION = 1


def _mapping(value):
    return value if isinstance(value, dict) else {}


def canonical_method(value):
    value = str(value or "").casefold()
    if value in {"pca", "pca (uncorrected)", "pca_uncorrected", "uncorrected"}:
        return "PCA"
    if value == "harmony":
        return "Harmony"
    if value == "rpca":
        return "RPCA"
    return None


def _space_method(space, assay, facts):
    if str(space).casefold() == "harmony":
        return "Harmony"
    if str(space).casefold() == "rpca":
        return "RPCA"
    if str(space).casefold() == "pca":
        if str(assay).casefold() == "integrated":
            # ★ ver77.0: RPCA は integrated assay の pca を使う。名前だけでは未補正と言えない。
            if facts.get("has_rpca_integration") or canonical_method(facts.get("wrapper_method")) == "RPCA":
                return "RPCA"
            return None
        if assay:
            return "PCA"
    return None


def _stage_complete(stages, *names):
    return any(_mapping(stages.get(n)).get("status") in {"complete", "completed"} for n in names)


def build_result_descriptor(source, facts, records=None):
    """ファイル名を証拠にせず、抽出した RDS の facts を分類する。"""
    facts, records = _mapping(facts), _mapping(records)
    reductions = _mapping(facts.get("reductions"))
    provenance = _mapping(facts.get("result_provenance"))
    misc = _mapping(facts.get("misc"))
    embedding = deepcopy(_mapping(facts.get("embedding")))
    clusters = deepcopy(_mapping(facts.get("clusters")))
    embedding.setdefault("kind", "umap" if facts.get("has_umap") else "none")
    if not embedding.get("space") and embedding.get("location") != "sidecar":
        embedding["space"] = provenance.get("embedding_space")
    embedding.setdefault("location", "primary")
    embedding.setdefault("parameters", {})
    if not clusters.get("space"):
        clusters["space"] = provenance.get("cluster_space") or misc.get("cluster_reduction")
    clusters.setdefault("parameters", {})
    for item in (embedding, clusters):
        if not item.get("assay"):
            item["assay"] = _mapping(reductions.get(item.get("space"))).get("assay")
    evidence, conflicts = [], []
    declared = canonical_method(provenance.get("method") or misc.get("analysis_method"))
    observed_space = embedding.get("space") or clusters.get("space") or facts.get("wrapper_reduction")
    if embedding.get("kind") in {"pca2d", "none"} or embedding.get("origin_state") == "unknown":
        # ★ ver77.0: 診断用 PC1–PC2 は解析手法の証拠ではない。wrapper のない
        # 未完了 RPCA にも pca が残るため、補正 reduction が一意な場合だけ採用する。
        corrected_spaces = [name for name in reductions if str(name).casefold() in {"harmony", "rpca"}]
        unambiguous_space = (corrected_spaces[0] if len(corrected_spaces) == 1 else
                             "pca" if not corrected_spaces and "pca" in reductions else None)
        observed_space = (facts.get("wrapper_reduction") or
                          {"Harmony": "harmony", "RPCA": "rpca", "PCA": "pca"}.get(declared) or
                          (clusters.get("space") if facts.get("has_clusters") else None) or
                          unambiguous_space)
    observed_assay = _mapping(reductions.get(observed_space)).get("assay")
    inferred = _space_method(observed_space, observed_assay, facts)
    if declared:
        evidence.append("rds.result_provenance.method")
    if inferred:
        evidence.append("rds.reduction_assay_and_commands")
    if declared and inferred and declared != inferred:
        conflicts.append("RDS の記録手法と使用された reduction/assay が一致しません")
    if records.get("artifact_sha256") and records["artifact_sha256"] != source.get("sha256"):
        conflicts.append("手法記録の SHA-256 と RDS の内容が一致しません")
    method = declared or inferred
    state = "conflict" if conflicts else "recorded" if declared else "inferred" if inferred else "unknown"
    kind = clusters.get("kind") or provenance.get("cluster_kind")
    if provenance.get("cluster_kind") == "none" or not facts.get("has_clusters", bool(clusters)):
        kind = "none"
    elif (misc.get("pca_origin") == "legacy_derived" and provenance.get("cluster_kind") != "computed") or provenance.get("cluster_kind") == "inherited":
        kind = "inherited"
    elif clusters.get("space") and observed_space and clusters["space"] != observed_space:
        kind = "inherited"
    elif kind is None:
        kind = "computed" if facts.get("cluster_commands_verified") else "unknown"
    if facts.get("idents_match_seurat_clusters") is False and kind == "computed":
        kind = "manual"
    clusters["kind"] = kind
    if kind == "computed" and clusters.get("space") is None:
        clusters["kind"] = kind = "unknown"
    stages = _mapping(records.get("stages"))
    # ★ ver77.0: v1 の global failed で、保存済みの独立段階まで消してはならない。
    # v2 だけは明示した cluster 未完了を尊重し、古い残骸を表示可能にしない。
    cluster_stage = _mapping(stages.get("cluster") or stages.get("clustering"))
    cluster_ready = bool(facts.get("has_clusters", False)) and kind != "none" and (
        not stages or cluster_stage.get("status") in {"complete", "completed"})
    reduction = _mapping(reductions.get(observed_space))
    reduction_ready = bool(reduction.get("n_dims", 0)) and state in {"recorded", "inferred"}
    if source.get("n_cells") is not None and reduction.get("n_cells") != source["n_cells"]:
        reduction_ready = False
    coherent = state != "conflict"
    cells_valid = facts.get("cell_ids_valid", True)
    capabilities = {
        "diagnostics": True,
        "resume": reduction_ready and coherent and cells_valid,
        "umap": embedding.get("kind") == "umap" and coherent and cells_valid,
        "pc": embedding.get("kind") == "pca2d" and coherent and cells_valid,
        "spatial": cluster_ready and bool(facts.get("has_spatial")) and coherent and cells_valid,
        "independent_comparison": cluster_ready and kind == "computed" and state in {"recorded", "inferred"} and coherent and cells_valid and embedding.get("origin_state") != "unknown",
        "feature": bool(facts.get("has_expression")) and cells_valid,
        "deg": cluster_ready and bool(facts.get("has_expression")) and kind != "unknown" and coherent and cells_valid,
    }
    source = deepcopy(source)
    content = source.get("sha256")
    result_id = provenance.get("result_id") or ("sha256:" + content if content else None)
    return {
        "descriptor_version": DESCRIPTOR_VERSION,
        "result_id": result_id,
        "artifact_id": "sha256:" + content if content else None,
        "artifact_state": "verified" if content and cells_valid else "unverified",
        "source": source,
        "classification": {"method": method, "state": state, "reduction": observed_space,
                           "evidence": evidence, "conflicts": conflicts},
        "embedding": embedding, "clusters": clusters, "reductions": deepcopy(reductions),
        "capabilities": capabilities,
        "run_state": {"status": records.get("status", "unknown"), "stages": deepcopy(stages)},
    }


def descriptor_method(descriptor, fallback=None):
    classification = _mapping(_mapping(descriptor).get("classification"))
    if classification.get("state") == "conflict":
        return fallback
    return classification.get("method") or fallback


def descriptor_deg_method(descriptor):
    """対応を確かめられる独立クラスタの DEG 検索手法だけを返す。"""
    descriptor = _mapping(descriptor)
    if (not _mapping(descriptor.get("capabilities")).get("deg") or
            _mapping(descriptor.get("clusters")).get("kind") != "computed"):
        return None
    return descriptor_method(descriptor)


def descriptor_label(descriptor):
    descriptor = _mapping(descriptor)
    label = descriptor_method(descriptor) or "手法未確認"
    if _mapping(descriptor.get("classification")).get("state") == "conflict":
        label = "由来情報の不一致"
    clusters = _mapping(descriptor.get("clusters"))
    if clusters.get("kind") == "inherited":
        label += "（既存クラスタを使用）"
    elif clusters.get("kind") == "unknown":
        label += "（クラスタ由来未確認）"
    if _mapping(descriptor.get("embedding")).get("kind") == "pca2d":
        label += " / PC1–PC2"
    if _mapping(descriptor.get("embedding")).get("origin_state") == "unknown":
        label += "（埋め込み由来未確認）"
    return label


def require_capabilities(descriptor, purpose):
    # ★ ver77.0: unknown/conflict を独立手法として比較しない。診断用表示は別能力。
    purposes = {"comparison": "independent_comparison", "independent": "independent_comparison"}
    capability = purposes.get(purpose, purpose)
    if not _mapping(_mapping(descriptor).get("capabilities")).get(capability, False):
        raise ValueError(f"{descriptor_label(descriptor)}: {purpose} に必要な結果が検証されていません")
    return descriptor


def sequence_sha256(values):
    """区切り文字を含む ID でも曖昧にならない長さ付きハッシュ。"""
    digest = hashlib.sha256()
    for value in values:
        payload = str(value).encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def contract_signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
