"""データ出力の進捗ジョブレジストリのテスト（Dash 非依存）。

対象: app.services.export_progress
- % のクランプ(0-99)と単調増加
- ラベル更新 / running 以外は無視
- finish/fail の状態遷移
- get_job のコピー隔離、pop、上限掃除
"""
from app.services import export_progress as ep


def test_update_clamps_and_monotonic():
    jid = ep.new_job()
    try:
        seq = []
        for p in [5, 10, 30, 52, 58, 90, 150, -5, 95]:
            ep.update_job(jid, p, "step")
            seq.append(ep.get_job(jid)["pct"])
        # 0-99 にクランプ、かつ単調非減少（過去値より下げない）
        assert all(0 <= v <= 99 for v in seq)
        assert seq == sorted(seq)
        assert max(seq) == 99  # 150 → 99 にクランプ
    finally:
        ep.pop_job(jid)


def test_label_updates_and_get_is_copy():
    jid = ep.new_job()
    try:
        ep.update_job(jid, 20, "手法クラスタを準備中… (RPCA)")
        snap = ep.get_job(jid)
        assert snap["label"] == "手法クラスタを準備中… (RPCA)"
        # get_job はコピー：外で書き換えても内部に影響しない
        snap["pct"] = 999
        assert ep.get_job(jid)["pct"] == 20
    finally:
        ep.pop_job(jid)


def test_finish_and_fail_transitions():
    jid = ep.new_job()
    try:
        ep.update_job(jid, 60, "書き込み中… 3/5")
        ep.finish_job(jid, {"content": "..."}, "✅ 完了")
        d = ep.get_job(jid)
        assert d["status"] == "done" and d["pct"] == 100 and d["download"]
        # 完了後は update を無視（running でない）
        ep.update_job(jid, 10, "x")
        assert ep.get_job(jid)["pct"] == 100
    finally:
        ep.pop_job(jid)

    jid2 = ep.new_job()
    try:
        ep.fail_job(jid2, "❌ エラー")
        d = ep.get_job(jid2)
        assert d["status"] == "error" and "エラー" in d["msg"]
    finally:
        ep.pop_job(jid2)


def test_pop_and_missing():
    jid = ep.new_job()
    ep.pop_job(jid)
    assert ep.get_job(jid) is None
    # 未知IDへの操作は例外を出さない
    ep.update_job("nope", 50, "x")
    ep.finish_job("nope", None, "x")
    assert ep.get_job("nope") is None


def test_max_jobs_cleanup_keeps_running():
    # 上限を超えて作成しても、完了済みが掃除され running は残る
    ids = [ep.new_job() for _ in range(ep._MAX_JOBS + 5)]
    try:
        # いくつか完了させておく
        for j in ids[:10]:
            ep.finish_job(j, {"c": 1}, "done")
        # さらに新規作成 → 掃除が走る
        newj = ep.new_job()
        assert ep.get_job(newj) is not None
        # レジストリ総数は上限付近に収まる
        assert len(ep._JOBS) <= ep._MAX_JOBS + 1
    finally:
        for j in ids + [newj]:
            ep.pop_job(j)
