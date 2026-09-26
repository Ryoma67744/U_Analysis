#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(path_name: str | Path, old: str, new: str) -> None:
    path = Path(path_name)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"patch target count for {path}: {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def before(apply_path: Path) -> None:
    old = '''if help_text.count(old) != 1:
    raise SystemExit("help limitation paragraph not found")
write(help_path, help_text.replace(old, new, 1))'''
    new = '''if help_text.count(old) != 1:
    old = "</div>"
    new = ("  <p class=\\\"small\\\">processed-centroidの可変長peak listは、"
           "m/zアライメント(ppm)でmaster featureへ対応付け、未記録featureを0とする"
           "共通行列へ変換します。0はexported spectrumへの未記録であり、絶対的不在では"
           "ありません。MS/MS・追加次元は集約しません。</p>\\n</div>")
write(help_path, help_text.replace(old, new, 1))'''
    replace_once(apply_path, old, new)


def after() -> None:
    # 既存テストのconversion_spec monkeypatch（引数なしlambda）を壊さず、
    # 実行時のprocessed alignment ppmだけを派生specへ固定する。
    replace_once(
        "App/app/services/input_preparation.py",
        '''    current_spec = deepcopy(
        spec if spec is not None else
        conversion_spec(processed_alignment_ppm=alignment_ppm)
    )
''',
        '''    if spec is None:
        current_spec = deepcopy(conversion_spec())
        current_spec["processed_alignment_ppm"] = float(alignment_ppm)
    else:
        current_spec = deepcopy(spec)
''',
    )
    replace_once(
        "App/app/services/input_preparation.py",
        '''            except (TypeError, ValueError) as exc:
                raise InputPreparationError(
                    "m/zアライメント(ppm)は数値で指定してください。"
                ) from exc
            options = dict(kwargs)
            options.setdefault("alignment_ppm", alignment_ppm)
            options.setdefault(
                "spec", conversion_spec(processed_alignment_ppm=alignment_ppm)
            )
''',
        '''            except (TypeError, ValueError) as exc:
                raise InputPreparationError(
                    "m/zアライメント(ppm)は数値で指定してください。"
                ) from exc
            if not math.isfinite(alignment_ppm) or alignment_ppm < 0:
                raise InputPreparationError(
                    "m/zアライメント(ppm)は0以上の有限値で指定してください。"
                )
            options = dict(kwargs)
            options.setdefault("alignment_ppm", alignment_ppm)
            if "spec" not in options:
                current_spec = deepcopy(conversion_spec())
                current_spec["processed_alignment_ppm"] = alignment_ppm
                options["spec"] = current_spec
''',
    )

    # pyimzML writerは合成fixtureへMS level=0を出力する。実装本体を緩めず、
    # processed-centroidの新規試験だけを明示MS1へ正規化する。
    replace_once(
        "App/tests/test_imzml_processed_sparse.py",
        '''import json
import zipfile

import numpy as np
''',
        '''import json
import zipfile
import xml.etree.ElementTree as ET

import numpy as np
''',
    )
    replace_once(
        "App/tests/test_imzml_processed_sparse.py",
        '''        for index, axis in enumerate(axes):
            writer.addSpectrum(np.asarray(axis, dtype=np.float64),
                               np.asarray(intensities[index], dtype=np.float32),
                               coords[index])
    return path
''',
        '''        for index, axis in enumerate(axes):
            writer.addSpectrum(np.asarray(axis, dtype=np.float64),
                               np.asarray(intensities[index], dtype=np.float32),
                               coords[index])
    tree = ET.parse(path)
    levels = [elem for elem in tree.iter() if elem.get("accession") == "MS:1000511"]
    if not levels:
        raise AssertionError("合成fixtureにMS level属性がありません")
    for level in levels:
        if level.get("value") == "0":
            level.set("value", "1")
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return path
''',
    )

    for name in [
        "App/app/services/imzml_io.py",
        "App/app/services/imzml_validation.py",
        "App/app/services/input_preparation.py",
        "App/docs/IMZML_AUTOIMPORT.md",
        "App/tests/test_imzml_processed_sparse.py",
        "App/tests/test_imzml_spatial_static_contract.py",
    ]:
        path = Path(name)
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: post_patch.py before APPLY_PATH | after")
    mode = sys.argv[1]
    if mode == "before":
        if len(sys.argv) != 3:
            raise SystemExit("before requires apply script path")
        before(Path(sys.argv[2]))
    elif mode == "after":
        after()
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
