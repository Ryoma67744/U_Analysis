"""Tests for hne_overlay.msi_to_hne_px (P4: MSI spots -> H&E pixel projection)."""

import numpy as np

from app.services.hne_overlay import (
    msi_to_hne_px, estimate_affine, apply_affine, invert_affine)


# 3 対応点。hne(px) -> tic(MSI) のアフィン推定に使う。
TIC = [[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]]       # MSI 座標
HNE = [[0.0, 0.0], [20.0, 0.0], [0.0, 20.0]]       # H&E px (= MSI の 2 倍スケール)


class TestMsiToHnePx:
    def test_too_few_landmarks_returns_none(self):
        assert msi_to_hne_px([1.0], [1.0], None, HNE[:2], TIC[:2]) is None

    def test_identity_landmarks(self):
        # hne == tic（恒等）なら MSI 座標がそのまま px 座標
        out = msi_to_hne_px([5.0, 7.0], [3.0, 1.0], None, TIC, TIC)
        assert out is not None
        px_x, px_y = out
        np.testing.assert_allclose(px_x, [5.0, 7.0], atol=1e-6)
        np.testing.assert_allclose(px_y, [3.0, 1.0], atol=1e-6)

    def test_scale_2x(self):
        # H&E px = MSI*2 なので MSI(5,5) -> px(10,10)
        out = msi_to_hne_px([5.0], [5.0], None, HNE, TIC)
        px_x, px_y = out
        np.testing.assert_allclose(px_x, [10.0], atol=1e-6)
        np.testing.assert_allclose(px_y, [10.0], atol=1e-6)

    def test_maps_landmark_msi_to_its_hne(self):
        # 各 tic ランドマーク(MSI) は、回転なしのとき対応する hne ランドマーク(px) に写る
        msi = np.asarray(TIC, dtype=float)
        out = msi_to_hne_px(msi[:, 0], msi[:, 1], None, HNE, TIC)
        px_x, px_y = out
        expected = np.asarray(HNE, dtype=float)
        np.testing.assert_allclose(px_x, expected[:, 0], atol=1e-6)
        np.testing.assert_allclose(px_y, expected[:, 1], atol=1e-6)


class TestAffineRoundTrip:
    def test_invert_affine_roundtrip(self):
        M = estimate_affine(HNE, TIC)            # px -> MSI
        M_inv = invert_affine(M)                  # MSI -> px
        pts = np.asarray([[1.0, 2.0], [3.0, 4.0]])
        back = apply_affine(apply_affine(pts, M), M_inv)
        np.testing.assert_allclose(back, pts, atol=1e-6)


# ---------------------------------------------------------------------------
# 組織像タイルのスポットサイズ自動算出 (ver66.0)
# ---------------------------------------------------------------------------

class TestAutoHneMarkerSize:
    """★ ver66.0: スポットサイズのスライダーを撤去したので、実データの
    間隔から決める必要がある。従来は固定値 5 だった。

    `_calc_zero_gap_marker_size` をそのまま当てられない（あれは x のユニーク値の
    最小差を間隔とみなすので、任意角のアフィンで回った座標では極端に小さくなる）。
    MSI 側の格子間隔に射影の拡大率を掛ける形にしてある。
    """

    def _grid(self, n=8, step=1.0):
        gx, gy = np.meshgrid(np.arange(n) * step, np.arange(n) * step)
        return gx.ravel().astype(float), gy.ravel().astype(float)

    def test_scales_with_the_projection(self):
        """★ 射影で 2 倍に広がれば、スポットも 2 倍の間隔ぶん大きくなること。

        固定値 5 に戻すとこのテストが落ちる。
        """
        from app.callbacks.interactive_hne_bg import _auto_hne_marker_size

        mx, my = self._grid()
        # y 方向の広がり（＝画面 px への換算の分母）を揃えたまま、
        # 「同じ広がりに対して間隔が 2 倍」の状況を作る（＝点が粗い）。
        dense = _auto_hne_marker_size(mx, my, mx, my)
        coarse = _auto_hne_marker_size(mx[::2] * 2, my[::2] * 2,
                                       mx[::2] * 2, my[::2] * 2)
        assert coarse > dense * 1.5, (dense, coarse)

    def test_is_invariant_to_rotation(self):
        """★ 回転しても大きさが変わらないこと（x の最小差では成立しない）。"""
        from app.callbacks.interactive_hne_bg import _auto_hne_marker_size

        mx, my = self._grid()
        theta = np.radians(37.0)          # 90 度の倍数でない角度
        rx = np.cos(theta) * mx - np.sin(theta) * my
        ry = np.sin(theta) * mx + np.cos(theta) * my
        straight = _auto_hne_marker_size(mx, my, mx, my)
        rotated = _auto_hne_marker_size(mx, my, rx, ry)
        # y 方向の広がりが回転で変わるぶんはずれるが、桁は変わらない
        assert 0.5 < rotated / straight < 2.0, (straight, rotated)

    def test_degenerate_input_falls_back(self):
        """点が少ない・広がりが無い等では従来の既定値へ落ちること。"""
        from app.callbacks.interactive_hne_bg import _auto_hne_marker_size

        assert _auto_hne_marker_size([0.0], [0.0], [0.0], [0.0]) == 5
        z = np.zeros(9)
        assert _auto_hne_marker_size(z, z, z, z) == 5
