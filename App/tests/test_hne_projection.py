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
