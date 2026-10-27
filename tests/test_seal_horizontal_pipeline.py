"""Manual absence skips type-row searches while ring geometry/OCR remains."""
from pathlib import Path
import shutil

import cv2
import numpy as np
import pytest

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.recognition.seal.layout_settings import seal_layout_settings_scope, seal_requirement_layout
from receipt_ocr.recognition.seal.preprocess import inputs


@pytest.mark.parametrize('shape',['round','ellipse'])
@pytest.mark.parametrize('setting',['absent','present','unmarked'])
def test_saved_setting_selects_search_or_ring_only_without_replacing_ocr(monkeypatch,tmp_path,shape,setting):
    source=tmp_path/'source.png'
    image=np.full((240,300,3),255,np.uint8)
    cv2.ellipse(image,(150,120),(130,95),0,0,360,(0,0,255),4)
    cv2.imwrite(str(source),image)
    searches=[]
    monkeypatch.setattr(inputs,'classify_shape',lambda *args:shape)
    monkeypatch.setattr(inputs,'save_region_crop',lambda s,d,r:shutil.copyfile(s,d))
    monkeypatch.setattr(inputs,'save_color_isolated_seal',lambda s,d,r:shutil.copyfile(s,d))
    monkeypatch.setattr(inputs,'prepare_pale_grid',lambda *args,**kwargs:None)
    def orient(*args,**kwargs):
        searches.append('orientation')
        return None,{'mode':'combined','rotation_steps':[],'type_band_text':'收货专用章'}
    monkeypatch.setattr(inputs,'prepare_round_stamp_combined',orient)
    monkeypatch.setattr(inputs,'prepare_horizontal_consensus',lambda *args,**kwargs:None)
    monkeypatch.setattr(inputs,'enhance_oriented_seal',lambda *args,**kwargs:None)
    def zones(*args,**kwargs):
        searches.append('zones');return None
    monkeypatch.setattr(inputs,'prepare_multi_zone',zones)
    monkeypatch.setattr(inputs,'trace_ellipse_border',lambda *args,**kwargs:None)
    monkeypatch.setattr(inputs,'save_ring_text_band_preview',lambda s,d,*args,**kwargs:shutil.copyfile(s,d))
    monkeypatch.setattr(inputs,'save_unwrapped_seal',lambda s,d,*args,**kwargs:shutil.copyfile(s,d))
    monkeypatch.setattr(inputs,'prepare_round_rim',lambda *args,**kwargs:(None,{'status':'not_applicable'}))
    monkeypatch.setattr(inputs,'prepare_han_ring',lambda *args,**kwargs:None)
    monkeypatch.setattr(inputs,'prepare_strong_border_ring',lambda *args,**kwargs:None)
    monkeypatch.setattr(inputs,'prepare_oval_ring',lambda *args,**kwargs:None)
    monkeypatch.setattr(inputs,'save_company_ring_input',lambda *args,**kwargs:None)
    monkeypatch.setattr(inputs,'save_ring_with_blank_seam',lambda *args,**kwargs:{'applied':False})
    rule={'id':7,'revision':4,'shape':shape,'requirement':'测试有限公司','horizontal_text':setting}
    directory=tmp_path/'artifacts';directory.mkdir()
    with seal_layout_settings_scope([rule]),seal_requirement_layout('测试有限公司'):
        artifact,jobs=inputs.prepare_inputs(source,SealRegion(0,0,1,1,'red','客户章',0),0,
            directory,'/files/artifacts/test','paddle_v6','combined')
    assert not artifact['errors']
    assert [item['channel'] for item,_ in jobs]==['ring']
    assert all(Path(path).is_file() for _,path in jobs)
    if setting=='absent':
        assert searches==[]
        assert artifact['horizontal_text_policy']['standard_id']==7
        assert artifact['horizontal_text_policy']['skipped_horizontal_search'] is True
        assert artifact['orientation']['method']=='manual_standard_ring_only'
    else:
        assert searches==['orientation','zones']
        assert 'manual_standard_ring_only'!=artifact['orientation'].get('method')
    # The manual setting supplies a route only; no requested company wording
    # has been injected as a fake read or matching candidate.
    assert artifact['reads']==[] and artifact.get('unwrapped_text')==''


def test_pipeline_binds_only_the_current_requirement_while_preparing_each_stamp(monkeypatch,tmp_path):
    from receipt_ocr.recognition.seal import pipeline
    from receipt_ocr.recognition.seal.layout_settings import explicit_horizontal_text_policy
    from receipt_ocr.domain.ocr import TextObservation
    image=tmp_path/'ring.png';cv2.imwrite(str(image),np.full((50,300,3),255,np.uint8))
    seen=[]
    def prepare(*args):
        seen.append(explicit_horizontal_text_policy('round'))
        return {'index':0,'region_id':'seal-0','shape_code':'round','color':'red','inputs':[],
                'reads':[],'errors':[],'multi_zone_layout':{}},[]
    monkeypatch.setattr(pipeline,'prepare_inputs',prepare)
    monkeypatch.setattr(pipeline,'read_double_border',lambda *args,**kwargs:False)
    monkeypatch.setattr(pipeline,'read_continuous_company',lambda *args,**kwargs:False)
    monkeypatch.setattr(pipeline,'finalize_multi_zone',lambda *args:[])
    monkeypatch.setattr(pipeline,'merge_number_read',lambda *args:None)
    monkeypatch.setattr(pipeline,'serial_matching_texts',lambda *args:[])
    monkeypatch.setattr(pipeline,'recognize_receiving_form',lambda *args,**kwargs:None)
    with seal_layout_settings_scope([{'id':1,'revision':2,'shape':'round','requirement':'测试有限公司','horizontal_text':'absent'}]):
        pipeline._recognize_local_seals(image,[],[object()],tmp_path,'/files/artifacts/test','paddle_v6',requirement='测试有限公司')
        pipeline._recognize_local_seals(image,[],[object()],tmp_path,'/files/artifacts/test','paddle_v6',requirement='其他有限公司')
    assert seen[0]['setting']=='absent' and seen[1] is None
