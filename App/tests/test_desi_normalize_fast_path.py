"""正規DESIの早期終了が入出力の意味とCSV論理行の解釈を変えないことを守る。"""

import hashlib

import pytest

from app.services import desi_converter as dc


@pytest.mark.parametrize("content", [
    b"\n\t\t\t1\n\t\t\t100.123456789\n\t\t\t100.123456789\n1\t0\t0\t5\tA\n",
    b"\r\n,,,1\r\n,,,100.123456789\r\n,,,100.123456789\r\n1,0,0,5,A\r\n",
    b"",
    b"\xef\xbb\xbfx,y,compound\n1,2,4\n",  # BOMの扱いを変更しない
    b"unknown,y,compound\n1,2,4\n",
    b"\t\t\t\n1\t2\t3\t4\n",
    b'"","",""\n1,2,4\n',
])
def test_noop_does_not_read_all_rows_or_change_any_byte(tmp_path, monkeypatch, content):
    path = tmp_path / "canonical.txt"
    path.write_bytes(content)
    before = path.stat()

    def unexpected_full_read(_path):
        pytest.fail("変換不要の正規ファイルを全文読み込みした")

    monkeypatch.setattr(dc, "_read_csv_rows", unexpected_full_read)
    assert dc.normalize_desi_txt(path) is False
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize("delimiter", ["\t", ",", ";"])
@pytest.mark.parametrize("first", ['x', ' X ', '"x\n"'])
def test_named_format_preserves_existing_conversion(tmp_path, monkeypatch, delimiter, first):
    content = (delimiter.join([first, "Y", '"compound_A"', '"compound_B"', "ROI"])
               + "\r\n" + delimiter.join(["1", "2", "3.123456789012345", "4", "A"])
               + "\r\n" + delimiter.join(["3", "4", "5", "6", "B"]) + "\r\n")
    path = tmp_path / "named.txt"
    path.write_bytes(content.encode())
    old_rows = dc._read_csv_rows(path)
    assert dc._is_named_format(old_rows)
    expected = "".join("\t".join(row) + "\n" for row in dc._reshape_named_format(old_rows))
    calls = []
    original = dc._read_csv_rows

    def read_all(p):
        calls.append(p)
        return original(p)

    monkeypatch.setattr(dc, "_read_csv_rows", read_all)
    assert dc.normalize_desi_txt(path) is True
    assert len(calls) == 1
    assert path.read_bytes() == expected.encode()
    assert dc.normalize_desi_txt(path) is False


def test_delimiter_probe_still_uses_ten_physical_lines(tmp_path):
    # 先頭だけならカンマ区切りに見えるが、従来の10行推定ではタブになる。
    path = tmp_path / "mixed.txt"
    content = b"x,y,name\n" + b"1\t2\t3\t4\t5\n" * 10
    path.write_bytes(content)
    assert not dc._is_named_format(dc._read_csv_rows(path))
    assert dc.normalize_desi_txt(path) is False
    assert path.read_bytes() == content


@pytest.mark.parametrize("content", [
    b"\n" + b"a" * (70 * 1024) + b"\n",  # 区切り推定が上限を超える
    b'"x' + b"\n" * 70000 + b'",y,name\n1,2,3\n',  # 論理行だけが上限を超える
    b"x,y,name\n1,2," + b"a" * 150000 + b"\n",  # 従来のCSVエラー経路
])
def test_large_or_invalid_header_keeps_full_reader_fallback(tmp_path, monkeypatch, content):
    path = tmp_path / "fallback.txt"
    path.write_bytes(content)
    original = dc._read_csv_rows
    calls = []

    def read_all(p):
        calls.append(p)
        return original(p)

    monkeypatch.setattr(dc, "_read_csv_rows", read_all)
    dc.normalize_desi_txt(path)
    assert calls == [path]


def test_large_canonical_file_has_bounded_text_reads(tmp_path, monkeypatch):
    # 行数を増やしても軽量判定が強度行全件を読み込まない。
    path = tmp_path / "large.txt"
    content = b"\n\t\t\t1\n\t\t\t100\n\t\t\t100\n" + b"1\t2\t3\t4.123456789\tA\n" * 100000
    path.write_bytes(content)
    before = hashlib.sha256(content).hexdigest()
    original = open
    reads = []

    class CountingReader:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            self.f.__enter__()
            return self

        def __exit__(self, *args):
            return self.f.__exit__(*args)

        def readline(self, *args):
            value = self.f.readline(*args)
            reads.append(len(value))
            return value

        def __iter__(self):
            return self

        def __next__(self):
            value = self.readline()
            if not value:
                raise StopIteration
            return value

        def __getattr__(self, name):
            return getattr(self.f, name)

    monkeypatch.setattr(dc, "open", lambda *a, **k: CountingReader(original(*a, **k)), raising=False)
    assert dc.normalize_desi_txt(path) is False
    assert sum(reads) < 1024
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
