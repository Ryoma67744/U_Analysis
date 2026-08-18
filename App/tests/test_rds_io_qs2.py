"""RDS シリアライザの qs2 移行 (ver57.1) のテスト

qs 0.27.3 は R 4.6.1 で `undefined symbol: SET_CLOENV` により dlopen できず、
ver50.1 以降ずっと gzip の saveRDS にフォールバックしていた
（実測: 1.03GB の Step2 で保存 162.8 秒 / 読込 29.1 秒）。
r2u の apt バイナリは Candidate = Installed = 0.27.3 で更新も来ないため、
後継の qs2 へ移した。

R は CI に無いので、構文・順序はソースを読んで検査し、実挙動は R がある
環境でのみ検証する（qs2 自体は R>=4.4 が要るため、ここで確認できるのは
フォールバック経路と後方互換）。
"""

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RDS_IO = _REPO_ROOT / "App" / "Script" / "helpers" / "rds_io.R"
_INSTALL_R = _REPO_ROOT / "App" / "install_r_packages.R"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SLIM = _REPO_ROOT / "App" / "Script" / "helpers" / "slim_existing_rds.R"

_RSCRIPT = shutil.which("Rscript")
_needs_r = pytest.mark.skipif(_RSCRIPT is None, reason="Rscript が無い環境")


@pytest.fixture(scope="module")
def rds_io_src() -> str:
    return _RDS_IO.read_text(encoding="utf-8")


def _function_body(src: str, name: str) -> str:
    """`name <- function(` から、次のトップレベル定義までを返す（粗いが十分）。

    次の定義は改行直後の `名前 <- function(` で探す。行頭アンカーだけだと
    自分自身の宣言行に当たって空文字を返す。
    """
    start = src.index(f"{name} <- function(")
    rest = src[start:]
    m = re.search(r"\n[.\w]+ <- function\(", rest)
    return rest[: m.start()] if m else rest


# ---------------------------------------------------------------------------
# rds_io.R の構造
# ---------------------------------------------------------------------------

class TestRdsIoStructure:

    def test_qs2_availability_check_exists(self, rds_io_src):
        assert ".rds_io_has_qs2 <- function()" in rds_io_src

    def test_legacy_qs_check_is_kept(self, rds_io_src):
        """旧 qs も残す。qs2 が無い環境でいきなり gzip まで落とさないため。"""
        assert ".rds_io_has_qs <- function()" in rds_io_src

    def test_reader_dispatch_helper_exists(self, rds_io_src):
        assert ".rds_io_read_qx <- function(path)" in rds_io_src

    def test_nthreads_helper_is_shared(self, rds_io_src):
        """スレッド数の決め方を 1 か所にまとめる（qs2 と qs で食い違わせない）。"""
        assert ".rds_io_nthreads <- function()" in rds_io_src
        assert "QS_NTHREADS" in _function_body(rds_io_src, ".rds_io_nthreads")

    def test_save_tries_qs2_before_qs(self, rds_io_src):
        """保存は qs2 が第一候補。順序が逆だと壊れた qs を毎回踏む。"""
        body = _function_body(rds_io_src, "save_rds_compact")
        assert body.index('name = "qs2"') < body.index('name = "qs"')

    def test_save_calls_qs2_save(self, rds_io_src):
        body = _function_body(rds_io_src, "save_rds_compact")
        assert "qs2::qs_save(" in body

    def test_save_falls_back_to_saverds(self, rds_io_src):
        """qs2 も qs も駄目なときに保存自体が失敗してはいけない。"""
        body = _function_body(rds_io_src, "save_rds_compact")
        assert "saveRDS(obj, tmp_path" in body

    def test_read_tries_qs2_before_qs(self, rds_io_src):
        body = _function_body(rds_io_src, ".rds_io_read_qx")
        assert body.index('name = "qs2"') < body.index('name = "qs"')
        assert "qs2::qs_read(" in body

    def test_read_error_names_both_reasons(self, rds_io_src):
        """どちらで失敗したか分からないと原因を追えない。"""
        body = _function_body(rds_io_src, ".rds_io_read_qx")
        assert "reasons" in body and "paste(reasons" in body

    def test_legacy_formats_still_recognised(self, rds_io_src):
        """既存の .rds 資産（gzip / xz / bzip2 / 無圧縮）を読めること。"""
        body = _function_body(rds_io_src, ".rds_io_legacy_format")
        for fmt in ("gzip", "bzip2", "xz", "無圧縮"):
            assert fmt in body

    def test_fallback_compress_is_overridable(self, rds_io_src):
        assert 'Sys.getenv("RDS_FALLBACK_COMPRESS"' in rds_io_src


# ---------------------------------------------------------------------------
# 周辺ファイルの整合
# ---------------------------------------------------------------------------

class TestPackagingIsConsistent:

    def test_install_list_has_qs2(self):
        src = _INSTALL_R.read_text(encoding="utf-8")
        assert '"qs2"' in src

    def test_install_list_dropped_broken_qs(self):
        """壊れた qs を入れ続けると、qs2 があっても事故の再発に気づけない。"""
        src = _INSTALL_R.read_text(encoding="utf-8")
        entries = re.findall(r'^\s*"([^"]+)",?\s*(?:#.*)?$', src, re.MULTILINE)
        assert "qs" not in entries
        assert "qs2" in entries

    def test_compose_passes_fallback_compress(self):
        """compose の environment に列挙しないと .env に書いても届かない。"""
        src = _COMPOSE.read_text(encoding="utf-8")
        assert "RDS_FALLBACK_COMPRESS=${RDS_FALLBACK_COMPRESS:-}" in src

    def test_compose_still_passes_qs_nthreads(self):
        src = _COMPOSE.read_text(encoding="utf-8")
        assert "QS_NTHREADS=${QS_NTHREADS:-}" in src

    def test_slim_tool_skips_qs2_files_too(self):
        src = _SLIM.read_text(encoding="utf-8")
        assert "qs/qs2" in src


# ---------------------------------------------------------------------------
# 実挙動（R がある環境のみ）
# ---------------------------------------------------------------------------

_R_HARNESS = """
source("{rds_io}")
d <- "{tmp}"
obj <- list(a = 1:500, b = letters, c = data.frame(x = 1:20))

fp <- file.path(d, "t.rds")
save_rds_compact(obj, fp)
cat("ROUNDTRIP:", identical(obj, load_rds_compact(fp)), "\\n")

for (cmp in c("gzip", "xz", "bzip2")) {{
  f <- file.path(d, paste0("old_", cmp, ".rds"))
  saveRDS(obj, f, compress = cmp)
  cat("LEGACY", cmp, .rds_io_legacy_format(f),
      identical(obj, load_rds_compact(f)), "\\n")
}}

fq <- file.path(d, "broken.rds")
writeBin(as.raw(c(0x0B, 0x0E, 0x0A, 0x0C, 0xFF)), fq)
cat("NONLEGACY:", .rds_io_is_qs_file(fq), "\\n")
e <- tryCatch(load_rds_compact(fq), error = function(e) conditionMessage(e))
cat("ERRMSG:", grepl("qs", e, fixed = TRUE), "\\n")
"""


def _run_r(script: str) -> str:
    proc = subprocess.run([_RSCRIPT, "-e", script], capture_output=True,
                          text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture(scope="module")
def r_harness_out(tmp_path_factory) -> str:
    """R を 1 回だけ起動して全ケース分の結果を集める（起動が毎回 8 秒かかるため）。"""
    if _RSCRIPT is None:
        pytest.skip("Rscript が無い環境")
    tmp = tmp_path_factory.mktemp("rdsio")
    return _run_r(_R_HARNESS.format(
        rds_io=_RDS_IO.as_posix(), tmp=tmp.as_posix()))


@_needs_r
class TestRoundTripWithRealR:
    """qs2 が無い環境（= フォールバック経路）と後方互換を実際に動かす。

    qs2 は R>=4.4 が要るため CI では入らない。ここで守りたいのは
    「qs2 が無くても保存・読込が壊れないこと」と「既存資産を読めること」。
    """

    def test_save_and_load_roundtrip(self, r_harness_out):
        assert "ROUNDTRIP: TRUE" in r_harness_out

    @pytest.mark.parametrize("cmp", ["gzip", "xz", "bzip2"])
    def test_legacy_files_still_readable(self, r_harness_out, cmp):
        assert f"LEGACY {cmp} {cmp} TRUE" in r_harness_out

    def test_non_legacy_file_is_detected(self, r_harness_out):
        assert "NONLEGACY: TRUE" in r_harness_out

    def test_unreadable_qs_file_raises_explicit_error(self, r_harness_out):
        """無言で readRDS に渡すと原因の分からない例外になる。"""
        assert "ERRMSG: TRUE" in r_harness_out


@_needs_r
def test_all_helper_scripts_parse():
    """R の構文チェック。CI に R が無いため、あるときだけ確実に見る。"""
    helpers = sorted((_REPO_ROOT / "App" / "Script" / "helpers").glob("*.R"))
    assert helpers, "ヘルパースクリプトが見つからない"
    paths = ", ".join(f'"{p.as_posix()}"' for p in helpers)
    _run_r(f"for (f in c({paths})) invisible(parse(f)); cat('OK\\n')")
