from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.recognition.seal.preprocess.component_noise import component_noise_candidate
from receipt_ocr.recognition.seal.preprocess import continuous_company as company
from receipt_ocr.recognition.seal.postprocess.multi_zone import finalize_multi_zone
from test_continuous_company_recovery import evidence
from test_continuous_company_arc import arc_image


def setup_components(tmp_path, monkeypatch):
    image, _, guide = arc_image(range(140, 401, 15))
    boxes = [[400, 400, 600, 440], [460, 450, 540, 490], [400, 520, 600, 560]]
    for x in range(400, 600, 8):
        for y in range(520, 560, 8):
            cv2.rectangle(image, (x, y), (x+2, y+2), (0, 0, 255), -1)
    cv2.imwrite(str(tmp_path/'upright.png'), image)
    cv2.imwrite(str(tmp_path/'masked.png'), image)
    monkeypatch.setattr(company, 'trace_ellipse_border', lambda *a, **kw: guide)
    a = evidence()
    a['multi_zone_layout'].update(method='ellipse_components', expected_inner_roles=[],
        rows=[dict(name=f'inner-{i+1}', box=box) for i,box in enumerate(boxes)])
    for i in range(2):
        a['inputs'][i].update(zone_name=f'inner-{i+1}')
    a['inputs'].append(dict(id='noise', channel='layout_inner', zone_name='inner-3',
                            status='completed', image_url='/test/noise.png'))
    a['reads'].append(dict(id='noise:0',input_id='noise',channel='layout_inner',text='にはまは',confidence=.32))
    return a, image


def test_noise_removed_from_active_rows_and_mask_but_raw_pixels_and_reads_remain(tmp_path, monkeypatch):
    a, image = setup_components(tmp_path, monkeypatch); original = deepcopy(a)
    candidate = component_noise_candidate(a, tmp_path, 0, '/test')
    assert a == original
    assert candidate['multi_zone_layout']['expected_inner_roles'] == ['type','number']
    assert len(candidate['multi_zone_layout']['rows']) == 2
    assert candidate['component_row_filter']['discarded_rows'][0]['text'] == 'にはまは'
    assert candidate['reads'][-1]['channel'] == 'layout_noise_diagnostic'
    assert candidate['reads'][-1]['text'] == original['reads'][-1]['text']
    mask = cv2.imread(str(tmp_path/'seal-0-zones-filtered-mask.png'),0)
    assert not mask[520:560,400:600].any()
    masked = cv2.imread(str(tmp_path/'seal-0-zones-filtered-ring-input.png'))
    np.testing.assert_array_equal(masked[520:560,400:600],image[520:560,400:600])
    np.testing.assert_array_equal(cv2.imread(str(tmp_path/'upright.png')), image)


@pytest.mark.parametrize('mode',['confident','chinese','number','phone','station','failed',
                                 'unread_type','real_glyphs','missing_read','unknown_row'])
def test_real_or_unchecked_third_rows_are_not_removed(tmp_path, monkeypatch, mode):
    a, image = setup_components(tmp_path, monkeypatch)
    if mode == 'confident': a['reads'][-1]['confidence']=.8
    elif mode == 'chinese': a['reads'][-1]['text']='维修服务'
    elif mode == 'number': a['reads'][-1]['text']='(2)'
    elif mode == 'phone': a['reads'][-1]['text']='电话:020-12345678'
    elif mode == 'station': a['reads'][-1]['text']='5785258站'
    elif mode == 'failed': a['inputs'][-1]['status']='failed'
    elif mode == 'unread_type': a['reads'][0]['confidence']=.7
    elif mode == 'unknown_row': a['reads'][-1].update(text='SERVICE',confidence=.7)
    elif mode == 'missing_read': a['reads'].pop()
    else:
        for x in range(405,590,35): cv2.rectangle(image,(x,520),(x+5,559),(0,0,255),-1)
        cv2.imwrite(str(tmp_path/'upright.png'), image)
    before=deepcopy(a)
    assert component_noise_candidate(a,tmp_path,0,'/test') is None
    assert a == before


def test_continuous_flow_reassembles_the_actual_number_and_records_original_split(tmp_path, monkeypatch):
    a, _ = setup_components(tmp_path, monkeypatch)
    name='深圳欧瑞特供应链管理有限公司天津分公司'
    reader=SimpleNamespace(read_line=lambda *a,**kw:[TextObservation(name,.99,0,0,1,1)])
    assert company.read_continuous_company(a,tmp_path,0,'/test',reader=reader,provider='paddle_v6')
    assert a['multi_zone_layout']['method']=='ellipse_continuous_name'
    assert a['continuous_company']['original_layout']['method']=='ellipse_components'
    assert finalize_multi_zone(a)==[name+'收货专用章(1)']
    assert a['stamp_number_text']=='(1)'
    assert len(a['multi_zone_layout']['rows'])==2


@pytest.mark.parametrize('mode',['empty','error','reliable_lower'])
def test_failed_retry_or_real_lower_text_never_commits_filtered_layout(tmp_path, monkeypatch, mode):
    a, _ = setup_components(tmp_path, monkeypatch)
    if mode=='reliable_lower': a['reads'][3].update(text='维修服务',confidence=.9)
    before=deepcopy(a)
    def read(*args,**kwargs):
        if mode=='error': raise RuntimeError('OCR失败')
        if mode=='reliable_lower': pytest.fail('should protect independent lower text')
        return []
    reader=SimpleNamespace(read_line=read)
    if mode=='error':
        with pytest.raises(RuntimeError):
            company.read_continuous_company(a,tmp_path,0,'/test',reader=reader,provider='paddle_v6')
    else:
        assert not company.read_continuous_company(a,tmp_path,0,'/test',reader=reader,provider='paddle_v6')
    assert a==before


def test_live_pipeline_filters_noise_before_continuous_read_and_keeps_raw_diagnostics(tmp_path, monkeypatch):
    from receipt_ocr.recognition.seal import pipeline
    directory=tmp_path/'seals';directory.mkdir()
    a,_=setup_components(directory,monkeypatch)
    raw=deepcopy(a['reads']);a['reads']=[]
    jobs=[]
    for entry in a['inputs']:
        row=next(row for row in raw if row['input_id']==entry['id'])
        entry['detected_rows']=[TextObservation(row['text'],row['confidence'],0,0,1,1).to_dict()]
        jobs.append((entry,directory/'unused.png'))
    name='深圳欧瑞特供应链管理有限公司天津分公司'
    monkeypatch.setattr(pipeline,'prepare_inputs',lambda *args,**kwargs:(a,jobs))
    monkeypatch.setattr(pipeline,'OcrReader',lambda:SimpleNamespace(
        read_line=lambda *args,**kwargs:[TextObservation(name,.99,0,0,1,1)]))
    texts,artifacts=pipeline._recognize_local_seals(tmp_path/'source.png',[],[object()],
        tmp_path,'/test','paddle_v6',requirement='完全不同的公司收货专用章(9)')
    assert name+'收货专用章(1)' in texts, (texts, artifacts[0].get('errors'), artifacts[0].get('reads'))
    assert raw[2]['text'] not in texts and 'にはまは' not in texts
    assert artifacts[0]['multi_zone_layout']['status']=='recognized'
    assert artifacts[0]['component_row_filter']['discarded_rows'][0]['text']=='にはまは'
    assert not artifacts[0]['errors']
