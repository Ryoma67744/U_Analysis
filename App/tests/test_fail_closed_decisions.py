"""「判定できない」を安全でない側に倒していた箇所 (ver52.3 ④)。

■ 共通する形

判定を `try` で囲み、失敗したときに **都合のよい側**へ倒していた。
どちらも「分からない」を「大丈夫」と読み替えている:

    上書きゲート  判定不能 → 「既存結果なし」→ **確認なしで上書き**
    注釈キャッシュ 判定不能 → 「キャッシュは新鮮」→ **古い化合物名が出続ける**

不確実性は、間違えたときの損害が小さい側へ倒すべき。
確認が 1 回余計に出るのと、前の解析結果が消えるのとでは損害が桁違いに違う。
キャッシュを作り直すのは遅くなるだけで、間違った名前は出さない。
"""

import logging

import pytest


# ---------------------------------------------------------------------------
# 上書き確認ゲート
# ---------------------------------------------------------------------------
class TestOverwriteGateFailsClosed:
    """★ この 1 件だけは「間違った結果」ではなく **データを壊す**。"""

    @staticmethod
    def _fn():
        from app.callbacks.analysis_callbacks import _output_has_existing_results
        return _output_has_existing_results

    def test_empty_folder_is_not_treated_as_existing(self, tmp_path):
        """前提の固定: 本当に空なら False（確認を出さない）。"""
        d = tmp_path / "empty"
        d.mkdir()
        assert self._fn()(str(d)) is False

    def test_missing_folder_is_not_treated_as_existing(self, tmp_path):
        assert self._fn()(str(tmp_path / "nope")) is False

    def test_analysis_params_marks_it_as_existing(self, tmp_path):
        d = tmp_path / "run"
        d.mkdir()
        (d / "analysis_params.json").write_text("{}", encoding="utf-8")
        assert self._fn()(str(d)) is True

    def test_undetectable_state_is_treated_as_existing(
            self, tmp_path, monkeypatch, caplog):
        """★ 本丸: 検出に失敗したら「既存あり」に倒すこと。

        修正前は例外を握りつぶして次の判定へ落ち、他が空なら False を返して
        いた。呼び出し側は False を「確認不要」として扱うので、
        **確認なしで前の解析結果を上書き**できてしまう。
        """
        import app.callbacks.interactive_callbacks as IC

        def _boom(_path):
            raise OSError("cannot scan reduction RDS")

        monkeypatch.setattr(IC, "_detect_integration_methods", _boom)

        d = tmp_path / "run"
        d.mkdir()          # analysis_params.json も RDS_Files も無い

        with caplog.at_level(logging.WARNING):
            got = self._fn()(str(d))

        assert got is True, (
            "既存結果を検出できなかったのに False を返している。"
            "呼び出し側は確認モーダルを出さないので、前の解析結果が"
            "**確認なしで上書きされる**")
        assert any("既存あり" in r.getMessage() for r in caplog.records), \
            "安全側に倒したことを記録していない"


# ---------------------------------------------------------------------------
# 注釈キャッシュの鮮度
# ---------------------------------------------------------------------------
class TestAnnotationCacheFreshnessFailsClosed:
    """★ 「古くないか」を判定するブロックが、不確実を「古くない」に倒していた。"""

    @pytest.fixture
    def bridge(self):
        from app.services.seurat_bridge import SeuratBridge
        return SeuratBridge()

    def _prepare(self, tmp_path, monkeypatch, bridge):
        """キャッシュとサイドカーを用意し、サイドカー検出を差し替える。"""
        import json

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        cache = cache_dir / "feature_annotations.json"
        cache.write_text(json.dumps({"mz_1": "OLD NAME"}), encoding="utf-8")
        sidecar = tmp_path / "sidecar.parquet"
        sidecar.write_bytes(b"x")
        monkeypatch.setattr(
            type(bridge), "_find_feature_annotation_sidecar",
            lambda self, rds: sidecar, raising=False)
        return cache_dir, cache, sidecar

    def test_fresh_cache_is_reused(self, tmp_path, monkeypatch, bridge):
        """前提の固定: 判定できてキャッシュが新しければ再利用する。"""
        import os

        cache_dir, cache, sidecar = self._prepare(tmp_path, monkeypatch, bridge)
        os.utime(sidecar, (1_700_000_000, 1_700_000_000))
        os.utime(cache, (1_800_000_000, 1_800_000_000))   # キャッシュの方が新しい
        got = bridge._load_feature_annotations(
            cache_dir, "/rds/x.rds", features_list=["mz_1"])
        assert got == {"mz_1": "OLD NAME"}, \
            "新しいキャッシュを再利用していない（性能が落ちる）"

    def test_undetectable_freshness_rebuilds(
            self, tmp_path, monkeypatch, bridge, caplog):
        """★ 本丸: mtime を読めないときはキャッシュを作り直すこと。

        修正前は `except OSError: cache_fresh = True` だったので、
        分子情報を後から付与しても**古い化合物名が出続けた**。
        """
        cache_dir, _cache, _sidecar = self._prepare(tmp_path, monkeypatch, bridge)

        class _UnstatableSidecar:
            """mtime を読めないサイドカー。

            `Path.stat` 全体を差し替えると `cache_file.exists()` など
            無関係な呼び出しまで壊れるので、**サイドカー側だけ**失敗させる。
            """

            def stat(self):
                raise OSError("stat failed")

        monkeypatch.setattr(
            type(bridge), "_find_feature_annotation_sidecar",
            lambda self, rds: _UnstatableSidecar(), raising=False)

        with caplog.at_level(logging.WARNING):
            got = bridge._load_feature_annotations(
                cache_dir, "/rds/x.rds", features_list=[])

        assert got != {"mz_1": "OLD NAME"}, (
            "鮮度を判定できないのに古いキャッシュを返している。"
            "分子情報を追加しても古い化合物名が出続ける")
        assert any("作り直す" in r.getMessage() for r in caplog.records), \
            "安全側に倒したことを記録していない"
