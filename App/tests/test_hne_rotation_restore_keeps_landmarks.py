"""H&E の対応点（ランドマーク）が不意に消えないことの番人。

★ ver56.5 / デバッグ総点検 §4.2 (C05-2):

  個体を切り替えた直後（またはページを開き直した直後）に、回転スライダや
  左右／上下反転のチェックに **一度触れただけ**で、その個体で苦労して打った
  対応点が全部消え、位置合わせ前の状態に戻ってしまう。保存済みデータも
  上書きされるため復旧できない。

■ なぜ起きるか

  `hne_restore_sample` は復元時に `hne_rotation_store` だけを戻し、
  **回転スライダ／反転チェックの表示値は戻していなかった**
  （「出力すると `hne_update_rotation` が発火して復元直後の対応点を消す恐れ」
    という理由がコメントに残っている）。

  その結果 store（例: 90°）と画面（0°）が食い違ったままになり、次に利用者が
  反転チェックを 1 回触ると `hne_update_rotation` が**スライダ側の 0°**を読んで
  「回転が変わった」と判定し、対応点を全消去したうえ回転まで 0° に巻き戻す。

■ 修正の要

  `hne_update_rotation` には既に「実質変化なしなら何もしない」同値ガードがある。
  したがってスライダを store と同じ値へ復元すれば、復元由来の発火は
  同値ガードに吸収され、対応点は消えない。コメントが懸念していた事故は
  このガードによって既に防がれている。
"""
import ast
import inspect

import pytest

import app.callbacks.hne_overlay_callbacks as hne


class TestRotationGuard:
    """同値ガード（これがあるからスライダを復元してよい）。"""

    def test_same_rotation_does_not_clear_landmarks(self):
        prev = {"angle": 90.0, "flip_h": True, "flip_v": False}
        rot, lm = hne.hne_update_rotation(90, ["flip_h"], prev)
        from dash import no_update
        assert rot is no_update and lm is no_update, (
            "回転が変わっていないのに対応点を消している")

    def test_real_change_still_clears_landmarks(self):
        """本当に回転が変わったときは従来どおり対応点を無効化すること。"""
        prev = {"angle": 90.0, "flip_h": False, "flip_v": False}
        rot, lm = hne.hne_update_rotation(90, ["flip_h"], prev)
        assert rot == {"angle": 90.0, "flip_h": True, "flip_v": False}
        assert lm == {"tic": [], "hne": []}, (
            "回転を変えたら旧対応点は無効にする必要がある")

    def test_angle_change_clears_landmarks(self):
        prev = {"angle": 0.0, "flip_h": False, "flip_v": False}
        rot, lm = hne.hne_update_rotation(180, [], prev)
        assert rot["angle"] == 180.0
        assert lm == {"tic": [], "hne": []}


class TestRestoreSyncsTheSliders:
    """★ 本丸: 復元時にスライダ表示も store と揃えること。"""

    def test_restore_outputs_slider_values(self):
        """`hne_restore_sample` が回転スライダ／反転チェックも出力すること。

        出力しないと store と画面が食い違い、次の 1 操作で対応点が消える。
        """
        tree = ast.parse(inspect.getsource(hne))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == "hne_restore_sample"):
                continue
            deco = ast.dump(node.decorator_list[0])
            assert "hne_rotation_angle" in deco, (
                "復元が回転スライダの表示値を戻していない。"
                "store と画面がずれ、次にスライダ/反転を触った瞬間に"
                "対応点が全消去される")
            assert "hne_rotation_flip" in deco, (
                "復元が反転チェックの表示値を戻していない")
            return
        pytest.fail("hne_restore_sample が見つからない")

    def test_restored_values_round_trip_through_the_guard(self):
        """復元した値でガードを通すと「変化なし」と判定されること。

        ここが崩れると、復元直後の発火で対応点が消える
        （コメントが懸念していた事故そのもの）。
        """
        from dash import no_update
        saved = {"angle": 90.0, "flip_h": True, "flip_v": True}
        # 復元がスライダへ流す値（実装と同じ組み立て方）
        angle = saved["angle"]
        flips = [k for k in ("flip_h", "flip_v") if saved[k]]
        rot, lm = hne.hne_update_rotation(angle, flips, saved)
        assert rot is no_update and lm is no_update, (
            "復元値でガードを通したのに対応点が消えている")
