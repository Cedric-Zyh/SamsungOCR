"""A mistaken dispatch template must not discard an observed customer box."""
from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace

from PIL import Image
import pytest

from receipt_ocr.application.requests import StageRequest
from receipt_ocr.recognition.seal.providers.qingtong import compare_qingtong_seal, qingtong_region_boxes
from receipt_ocr.stages import seal as seal_stage


REQUIRED = '三星授权（成都市欣金维电子）服务中心授权代码：4910964'
DISPATCH = '中国外运物流发展有限公司广州分公司商品付讫章'
# Exact provider OCR from 7303954809.jpg, including its mistakes and omissions.
OBSERVED = '三国挖权授权代码4910964173号附号院往北200米02地子服务中心'
CUSTOMER_BOX = [1055, 2270, 1936, 2939]


def response(text=OBSERVED, *, similarity=.671):
    return {'ok': True, 'response': {'data': {'img_0': [
        {'xyxy': [174, 1789, 835, 2225], 'cls': 'ellipse',
         'matched_seal': {'label': DISPATCH, 'similarity': .953},
         'text_formatted': DISPATCH},
        {'xyxy': CUSTOMER_BOX, 'cls': 'rectangle',
         'matched_seal': {'label': DISPATCH, 'similarity': similarity},
         'text_formatted': text},
    ]}}}


@pytest.mark.parametrize('similarity', [.0, .671, .99, 1.0, None, 'invalid'])
@pytest.mark.parametrize('text', [OBSERVED, '三星……服务中……', '', None])
def test_wrong_template_or_incomplete_ocr_keeps_one_full_box_without_claiming_match(text, similarity):
    external = response(text, similarity=similarity)
    before = deepcopy(external)
    check = compare_qingtong_seal(REQUIRED, external)
    assert check['status'] != '匹配'
    assert [c['index'] for c in check['dual_check']['candidates']] == [1]
    assert [c['index'] for c in check['dual_check']['excluded_candidates']] == [0]
    candidate = check['dual_check']['candidates'][0]
    assert candidate['template']['label'] == DISPATCH
    assert candidate['ocr']['text'] == (text or '')
    boxes = qingtong_region_boxes(check)
    assert len(boxes) == 1
    assert boxes[0]['xyxy'] == tuple(CUSTOMER_BOX)
    assert boxes[0]['api_index'] == 1
    assert boxes[0]['cls'] == 'rectangle'
    assert external == before


def test_complete_customer_ocr_can_match_despite_wrong_dispatch_template():
    check = compare_qingtong_seal(REQUIRED, response(REQUIRED))
    assert check['status'] == '匹配'
    assert check['recognized'] == REQUIRED
    assert check['dual_check']['selected']['best_channel'] == 'ocr'
    assert check['dual_check']['selected']['template']['status'] == '不匹配'


def test_known_dispatch_ocr_is_still_excluded_even_if_template_is_wrong():
    external = response(DISPATCH)
    external['response']['data']['img_0'][1]['matched_seal']['label'] = REQUIRED
    check = compare_qingtong_seal(REQUIRED, external)
    assert not qingtong_region_boxes(check)
    assert not check['dual_check']['candidates']
    assert len(check['dual_check']['excluded_candidates']) == 2
    assert check['status'] == '未识别'


def test_local_stage_reuses_retained_box_instead_of_detecting_fragments(tmp_path, monkeypatch):
    source = tmp_path / 'receipt.png'
    Image.new('RGB', (2480, 3594), 'white').save(source)
    future = Future()
    future.set_result(response())
    context = SimpleNamespace(source=source, page=lambda backend: [],
                              document_type={'type': 'receipt'}, allows_remote_seal=False)
    request = StageRequest(route={stage: 'paddle_v6' for stage in ('page', 'date', 'seal')},
                           fields={'签章要求': REQUIRED}, seal_future=future)
    seen = []

    def unexpected_detection(*args, **kwargs):
        pytest.fail('A retained QingTong box must prevent fallback region detection')

    def reader(source, rows, regions, *args, **kwargs):
        seen.extend(regions)
        return [], []

    monkeypatch.setattr(seal_stage, 'detect_seal_regions', unexpected_detection)
    result = seal_stage.recognize(context, request, reader,
                                  SimpleNamespace(skipped=lambda: {'enabled': False}))
    assert len(seen) == len(result.regions) == 1
    region = seen[0]
    assert (region.x * 2480, region.y * 3594,
            (region.x + region.width) * 2480,
            (region.y + region.height) * 3594) == pytest.approx(CUSTOMER_BOX)
    assert region.qingtong_cls == 'rectangle'
    assert result.check['region_source'] == '清瞳印章区域'
    assert result.check['status'] != '匹配'
