"""解析監視用の増分ログ読込。解析値・入力ファイルは変更しない。"""

import codecs
import os
import re
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path


MARKER_PREFIXES = ("[stage]", "[plan]", "[pass]")
# 旧形式の段階判定が使う末尾600行を保持する（画面の最大選択肢は200行）。
TAIL_LINES = 600
_LINE_END = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_READ_BYTES = 64 * 1024
_ANCHOR_BYTES = 128


@dataclass(frozen=True)
class LogSnapshot:
    """同じ読込時点の表示用末尾と、全期間の段階記録。"""

    lines: tuple[str, ...]
    markers: str
    revision: tuple
    bytes_read: int = 0

    def tail(self, last_n=50):
        return "\n".join(self.lines[-last_n:])


class _LogReader:
    def __init__(self):
        self.signature = None
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.lines = deque(maxlen=TAIL_LINES)
        self.markers = []
        self.pending = ""
        self.head = b""
        self.end = b""
        self.snapshot = LogSnapshot((), "", ())

    def _accept(self, text):
        text = self.pending + text
        start = 0
        for match in _LINE_END.finditer(text):
            # ★ ver66.3: CRLFが別の追記に分かれても空行を増やさない。
            if match.group() == "\r" and match.end() == len(text):
                break
            line = text[start:match.start()]
            self.lines.append(line)
            if line.lstrip().startswith(MARKER_PREFIXES):
                self.markers.append(line)
            start = match.end()
        self.pending = text[start:]

    def read(self, path):
        # ★ ver66.3: 全文を毎回splitlinesすると長時間解析ほど監視が重くなる。
        # 同じ世代は追記のみ読む。置換・切詰め・末尾の上書きは状態を作り直す。
        with Path(path).open("rb") as stream:
            stat = os.fstat(stream.fileno())
            signature = (stat.st_dev, stat.st_ino, stat.st_size,
                         stat.st_mtime_ns, stat.st_ctime_ns)
            if signature == self.signature:
                return LogSnapshot(self.snapshot.lines, self.snapshot.markers,
                                   self.snapshot.revision)
            bytes_read = 0
            reset = (self.signature is not None and
                     (signature[:2] != self.signature[:2] or
                      stat.st_size <= self.offset))
            if self.offset and not reset:
                # 同じinodeを切詰め直後に元サイズ以上まで書き直す場合も検出する。
                stream.seek(0)
                head = stream.read(len(self.head))
                stream.seek(self.offset - len(self.end))
                end = stream.read(len(self.end))
                bytes_read += len(head) + len(end)
                reset = head != self.head or end != self.end
            if reset:
                self.__init__()
            stream.seek(self.offset)
            while self.offset < stat.st_size:
                block = stream.read(min(_READ_BYTES, stat.st_size - self.offset))
                if not block:
                    break
                bytes_read += len(block)
                self.offset += len(block)
                self._accept(self.decoder.decode(block, final=False))
            stream.seek(0)
            self.head = stream.read(min(_ANCHOR_BYTES, self.offset))
            stream.seek(max(0, self.offset - _ANCHOR_BYTES))
            self.end = stream.read(min(_ANCHOR_BYTES, self.offset))
            bytes_read += len(self.head) + len(self.end)

        self.signature = signature
        # 未完行は表示・段階判定に仮に含め、改行到着時だけ累積へ確定する。
        pending = self.pending.rstrip("\r")
        lines = tuple(self.lines)
        markers = list(self.markers)
        if pending or self.pending:
            lines = (lines + (pending,))[-TAIL_LINES:]
            if pending.lstrip().startswith(MARKER_PREFIXES):
                markers.append(pending)
        self.snapshot = LogSnapshot(lines, "\n".join(markers), signature, bytes_read)
        return self.snapshot


_readers = OrderedDict()
_readers_lock = threading.RLock()
# 通常の解析は同時1件。監視の再接続と完了通知の並行処理にも余裕を持たせ、
# 古いジョブを監視し続けない場合にも保持件数を制限する。
_MAX_READERS = 8


def get_log_snapshot(log_file, generation=""):
    """世代が同じジョブの追記を読み、欠損・一時的読込失敗では空の状態を返す。"""
    if not log_file:
        return LogSnapshot((), "", ())
    key = (str(Path(log_file).absolute()), str(generation))
    with _readers_lock:
        reader = _readers.pop(key, None) or _LogReader()
        _readers[key] = reader
        while len(_readers) > _MAX_READERS:
            _readers.popitem(last=False)
        try:
            return reader.read(log_file)
        except (OSError, UnicodeError):
            # 一時的なUTF-8/読込エラー後も次のpollで全期間を復元する。
            _readers.pop(key, None)
            return LogSnapshot((), "", ())


def release_log_snapshot(log_file, generation=""):
    """終了した監視の末尾・マーカーを解放する。全文APIには影響しない。"""
    if log_file:
        key = (str(Path(log_file).absolute()), str(generation))
        with _readers_lock:
            _readers.pop(key, None)
