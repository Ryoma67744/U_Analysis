"""データ出力の進捗ポーリングが「画面を固めない」ことの回帰テスト（ver62.6）。

## 背景（実際に起きたこと）

利用者から「データ出力を押すと『準備中… 0%』から進まない」という報告があった。
画面はボタンが無効のまま、進捗バーが出たまま、理由の表示も無い、という状態で
**永久に**止まっていた。

原因は `data_export_poll` の「ジョブが見つからない」分岐。進捗ジョブは
`export_progress._JOBS`（モジュールグローバル）で持っているので、
**アプリのプロセスが再起動すると消える**。ブラウザのタブは開いたままなので
`data_export_job` ストアには前プロセスの job_id が残り、次のポーリングで
「見つからない」に落ちる。そこが `(no_update,) * 7 + (True,)` を返していたため、

  - ラベルは更新されない → 押した直後の文字が残り続ける
  - ボタンは無効のまま   → 押し直せない
  - ポーリングは停止     → 二度と更新されない

となり、画面が固まって理由もどこにも出なかった。

ここで守るのは「**失敗しても操作は必ず戻る**」という性質。
"""
import pytest
from dash import no_update

from app.callbacks.interactive_data_export import data_export_poll
from app.services import export_progress as ep

# data_export_poll の Output 並び（callback の宣言順と 1:1）
(I_URL, I_STATUS, I_CONTAINER, I_LABEL, I_BAR, I_ANIMATED,
 I_BTN_DISABLED, I_POLL_DISABLED) = range(8)


def _text(children) -> str:
    """html.Span 等から表示文字列を取り出す。"""
    if children is None or children is no_update:
        return ""
    if isinstance(children, str):
        return children
    inner = getattr(children, "children", None)
    if inner is None:
        return str(children)
    if isinstance(inner, (list, tuple)):
        return "".join(_text(c) for c in inner)
    return _text(inner)


def test_ジョブが消えてもボタンは押し直せる状態に戻る():
    """プロセス再起動でジョブが消えた状況を、pop で再現する。"""
    jid = ep.new_job()
    ep.pop_job(jid)  # ← プロセス再起動と同じ状態

    out = data_export_poll(1, {"job": jid})

    # 押し直せること。ここが no_update だと利用者は詰む。
    assert out[I_BTN_DISABLED] is False, "ボタンが無効のままでは押し直せない"
    assert out[I_POLL_DISABLED] is True, "無いジョブを叩き続けない"
    # 進捗表示は畳む（出たままだと「まだ動いている」と読める）
    assert out[I_CONTAINER] is not no_update
    assert out[I_CONTAINER].get("display") == "none"
    # ラベルが「準備中…」のまま残らないこと
    assert out[I_LABEL] is not no_update
    assert "準備中" not in _text(out[I_LABEL])


def test_ジョブが消えたときは理由が画面に出る():
    jid = ep.new_job()
    ep.pop_job(jid)

    out = data_export_poll(1, {"job": jid})

    msg = _text(out[I_STATUS])
    assert msg, "理由がどこにも出ないと、利用者は何が起きたか分からない"
    # 「何が起きたか」と「次に何をすればよいか」の両方を述べる
    assert "失われ" in msg or "再起動" in msg
    assert "もう一度" in msg or "再実行" in msg


def test_実行中はラベルと進捗が更新される():
    """正常系。running のジョブはラベル・% が更新され、ポーリングは続く。"""
    jid = ep.new_job()
    try:
        ep.update_job(jid, 42, "クラスタ突合中…")
        out = data_export_poll(1, {"job": jid})
        assert out[I_LABEL] == "クラスタ突合中…  42%"
        assert out[I_BAR] == 42
        assert out[I_POLL_DISABLED] is no_update, "実行中にポーリングを止めない"
    finally:
        ep.pop_job(jid)


def test_失敗したジョブはポーリングが遅れて届いても文言が消えない():
    """`error` のジョブを pop しないことの回帰テスト。

    ポーリング停止を返しても**既に飛んでいるリクエスト**が 1 回遅れて届くことが
    ある。従来は最初の 1 回で pop していたため、その遅れて届いた分が
    「ジョブの情報が失われました」に落ち、**本当のエラー文言を上書き**していた。
    """
    jid = ep.new_job()
    try:
        ep.fail_job(jid, "❌ 出力手法が 1 つも選ばれていません。")

        first = data_export_poll(1, {"job": jid})
        second = data_export_poll(2, {"job": jid})  # 遅れて届いた分

        assert "出力手法" in _text(first[I_STATUS])
        assert _text(second[I_STATUS]) == _text(first[I_STATUS]), (
            "遅れて届いたポーリングでエラー文言が別の文言に化けている")
    finally:
        ep.pop_job(jid)


def test_押下直後のラベルはポーリングのラベルと区別できる():
    """「まだ 1 度もポーリングが返っていない」と「0% が返ってきた」を見分けられること。

    従来はどちらも "準備中…  0%" で 1 バイトも違わなかった。そのため画面を見ても
    どちらか分からず、原因の切り分けで実際に取り違えた。
    """
    from app.callbacks.interactive_data_export import _START_LABEL

    jid = ep.new_job()
    try:
        polled = data_export_poll(1, {"job": jid})[I_LABEL]
        assert polled == "準備中…  0%"  # ポーリングが返した 0%
        assert _START_LABEL != polled, (
            "押下直後の表示とポーリング結果が同じ文字列だと、"
            "画面から切り分けができない")
    finally:
        ep.pop_job(jid)
