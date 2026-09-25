"""依存がある環境だけで実ParquetとimzML往復を確認。R/GUIの試験ではない。"""
from pathlib import Path
import json
import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pytest


@pytest.mark.parametrize('dtype',[np.float32,np.float64])
@pytest.mark.parametrize('mode',['continuous','processed'])
def test_real_four_pixel_registration_and_selected_export(tmp_path,dtype,mode):
    pq=pytest.importorskip('pyarrow.parquet', reason='actual Parquet backend is unavailable')
    pytest.importorskip('pyimzml', reason='actual imzML backend is unavailable')
    from pyimzml.ImzMLWriter import ImzMLWriter
    from pyimzml.ImzMLParser import ImzMLParser
    from app.services.input_preparation import prepare_imzml,validate_asset,InputPreparationError
    from app.services.imzml_validation import inspect_binary_contract
    from app.services.section_metadata import build_section_manifest
    from app.services.imzml_io import export_imzml
    xml=tmp_path/'sample.imzML'
    coordinates=[(6,10,1),(5,9,1),(6,9,1),(5,10,1)]
    axis=np.asarray([100.123456789,201.234567891,302.345678912],dtype=np.float64)
    values=np.asarray([[1,2,3],[4,5,6],[7,8,9],[10,11,12]],dtype=dtype)
    with ImzMLWriter(str(xml),mode=mode,spec_type='profile',mz_dtype=np.float64,intensity_dtype=dtype) as w:
        for c,v in zip(coordinates,values):w.addSpectrum(axis,v,c)
    # ★ ver70.0: pyimzMLのwriterはms level=0を出力するため、合成MS1 fixtureを明示する。
    # 検査本体を緩めず、0を拒否することを確認してからテスト原本の属性だけを変更する。
    tree=ET.parse(xml)
    levels=[e for e in tree.iter() if e.get('accession')=='MS:1000511']
    assert levels, '合成fixtureにMS level属性が必要です'
    assert all(e.get('value') in {'0','1'} for e in levels)
    if any(e.get('value')=='0' for e in levels):
        with pytest.raises(InputPreparationError, match='MS level=0'):
            inspect_binary_contract(xml)
    for level in levels:
        level.set('value','1')
    ET.register_namespace('', 'http://psi.hupo.org/ms/mzml')
    tree.write(xml,encoding='utf-8',xml_declaration=True)
    entry=build_section_manifest([{'path':str(xml),'available_rois':[]}])['files'][0]
    prepared=prepare_imzml(entry,cache_root=tmp_path/'cache')
    validate_asset(prepared)
    df=pq.read_table(prepared['runtime_path']).to_pandas()
    assert df[['id','x','y']].values.tolist()==[[1,5,9],[2,6,9],[3,5,10],[4,6,10]]
    columns=[f'{m:.6f}' for m in axis]
    np.testing.assert_array_equal(df[columns].values,values[[1,2,3,0]])
    assert df[columns].values.dtype==np.dtype(dtype)
    manifest=json.loads(Path(prepared['conversion_manifest_path']).read_text())
    np.testing.assert_array_equal(np.asarray(manifest['mz_axis']),axis)
    assert [m['source_index'] for m in manifest['source_coordinates']]==[1,2,3,0]
    receipt=export_imzml(prepared['runtime_path'],tmp_path/'out.zip',pixel_ids=[1,4])
    assert receipt['pixels']==2
    out=tmp_path/'export';out.mkdir()
    with zipfile.ZipFile(tmp_path/'out.zip') as z:z.extractall(out)
    with (out/'sample.ibd').open('rb') as binary:
        parser=ImzMLParser(str(out/'sample.imzML'),ibd_file=binary)
        assert parser.coordinates==[(5,9,1),(6,10,1)]
        for i,original in enumerate([1,0]):
            mz,arr=parser.getspectrum(i)
            np.testing.assert_array_equal(mz,axis)
            np.testing.assert_array_equal(arr,values[original])
    assert prepare_imzml(entry,cache_root=tmp_path/'cache')['conversion_key']==prepared['conversion_key']
