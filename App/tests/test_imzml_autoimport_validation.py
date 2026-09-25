"""境界検査の試験。標準ライブラリでXML/ibdを独立に組立てる（XSD適合性試験ではない）。"""
import hashlib
import struct
import uuid
import xml.etree.ElementTree as ET
import pytest
from app.services.imzml_validation import inspect_binary_contract, _bounded_block
from app.services.input_preparation import InputPreparationError, PreparationCancelled


def fixture_pair(tmp_path, width=4, refs=True):
    identity=uuid.UUID('00000000-1111-2222-3333-444444444444')
    binary=bytearray(identity.bytes)
    root=ET.Element('mzML')
    def cv(parent, term, value='', name=''):
        return ET.SubElement(parent,'cvParam', accession=term, value=str(value), name=name)
    content=ET.SubElement(ET.SubElement(root,'fileDescription'),'fileContent')
    cv(content,'IMS:1000080',str(identity));cv(content,'MS:1000579');cv(content,'MS:1000128');cv(content,'MS:1000130')
    group_list=ET.SubElement(root,'referenceableParamGroupList')
    for kind, term, w in [('mz','MS:1000514',8),('int','MS:1000515',width)]:
        g=ET.SubElement(group_list,'referenceableParamGroup',id=kind)
        cv(g,term);cv(g,'MS:1000523' if w==8 else 'MS:1000521');cv(g,'MS:1000576');cv(g,'IMS:1000101','true')
    spectra=ET.SubElement(ET.SubElement(root,'run'),'spectrumList',count='4')
    for i,(x,y) in enumerate([(2,2),(1,1),(2,1),(1,2)]):
        spectrum=ET.SubElement(spectra,'spectrum',id=f's{i}',index=str(i),defaultArrayLength='2')
        scan=ET.SubElement(ET.SubElement(spectrum,'scanList'),'scan')
        for term,val in [('IMS:1000050',x),('IMS:1000051',y),('IMS:1000052',1)]:cv(scan,term,val)
        arrs=ET.SubElement(spectrum,'binaryDataArrayList')
        for kind, w, values in [('mz',8,[100.,101.]),('int',width,[float(i+1),float(i+10)])]:
            arr=ET.SubElement(arrs,'binaryDataArray')
            ET.SubElement(arr,'referenceableParamGroupRef',ref=kind)
            offset=len(binary);encoded=struct.pack('<'+('d' if w==8 else 'f')*2,*values);binary.extend(encoded)
            for term,val in [('IMS:1000102',offset),('IMS:1000103',2),('IMS:1000104',len(encoded))]:cv(arr,term,val)
            ET.SubElement(arr,'binary')
    cv(content,'IMS:1000091',hashlib.sha1(binary).hexdigest())
    xml=tmp_path/'independent.imzML'; xml.with_suffix('.ibd').write_bytes(binary)
    ET.ElementTree(root).write(xml,encoding='utf-8')
    return xml,root


def save(xml,root): ET.ElementTree(root).write(xml,encoding='utf-8')
def terms(root,term):return [e for e in root.iter('cvParam') if e.get('accession')==term]


@pytest.mark.parametrize('width',[4,8])
def test_valid_independent_xml_ibd(width,tmp_path):
    p,_=fixture_pair(tmp_path,width)
    result=inspect_binary_contract(p)
    assert {k: result[k] for k in ('pixels','features','intensity_bytes','spectrum_type','polarity','uuid_verified','checksums_verified')} == dict(pixels=4,features=2,intensity_bytes=width,spectrum_type='profile',polarity='positive',uuid_verified=True,checksums_verified=['sha1'])
    assert result['spatial_layout']['component_count'] == 1
    assert result['spatial_layout']['pixel_count'] == 4


@pytest.mark.parametrize('term,value',[
 ('IMS:1000080','00000000-1111-2222-3333-555555555555'),('IMS:1000091','0'*40),
 ('IMS:1000102','99999'),('IMS:1000102','1'),('IMS:1000103','3'),('IMS:1000104','2'),
 ('IMS:1000101','false'),('IMS:1000050','-1'),('IMS:1000050','1.5'),('IMS:1000052','2')])
def test_invalid_attributes_rejected(tmp_path,term,value):
    p,root=fixture_pair(tmp_path);terms(root,term)[0].set('value',value);save(p,root)
    with pytest.raises(InputPreparationError):inspect_binary_contract(p)


@pytest.mark.parametrize('mutation',['ms2','precursor','extra_array','mixed_polarity','mixed_mode','mobility','duplicate','count','bad_ref','no_ms1','mixed_dtype','truncated'])
def test_unsupported_or_inconsistent(tmp_path,mutation):
    p,r=fixture_pair(tmp_path);spectra=list(r.iter('spectrum'))
    if mutation=='ms2':ET.SubElement(spectra[0],'cvParam',accession='MS:1000511',value='2')
    if mutation=='precursor':ET.SubElement(spectra[0],'precursor')
    if mutation=='extra_array':ET.SubElement(spectra[0].find('binaryDataArrayList'),'binaryDataArray')
    if mutation=='mixed_polarity':ET.SubElement(spectra[0],'cvParam',accession='MS:1000129',value='')
    if mutation=='mixed_mode':ET.SubElement(spectra[0],'cvParam',accession='MS:1000127',value='')
    if mutation=='mobility':ET.SubElement(spectra[0],'cvParam',accession='TEST:1',value='1',name='ion mobility')
    if mutation=='duplicate':
        terms(spectra[1],'IMS:1000050')[0].set('value','2');terms(spectra[1],'IMS:1000051')[0].set('value','2')
    if mutation=='count':next(r.iter('spectrumList')).set('count','5')
    if mutation=='bad_ref':next(spectra[0].iter('referenceableParamGroupRef')).set('ref','missing')
    if mutation=='no_ms1':terms(r,'MS:1000579')[0].set('accession','TEST:2')
    if mutation=='mixed_dtype':
        array=list(spectra[1].iter('binaryDataArray'))[0]
        array.remove(array.find('referenceableParamGroupRef'))
        for term,value in [('MS:1000514',''),('MS:1000521',''),('IMS:1000101','true')]:ET.SubElement(array,'cvParam',accession=term,value=value)
        terms(array,'IMS:1000104')[0].set('value','8')
    if mutation=='truncated':p.with_suffix('.ibd').write_bytes(b'short')
    save(p,r)
    with pytest.raises(InputPreparationError):inspect_binary_contract(p)


def test_cancel_header_inspection(tmp_path):
    p,_=fixture_pair(tmp_path)
    with pytest.raises(PreparationCancelled):inspect_binary_contract(p,cancel=lambda:True)


def test_large_feature_block_budget():
    assert _bounded_block(29127,8,128,64)==96
    assert _bounded_block(100,4,128,64)==128
    with pytest.raises(InputPreparationError):_bounded_block(999999999,8,128,1)
    with pytest.raises(InputPreparationError):_bounded_block(10,4,0,1)
