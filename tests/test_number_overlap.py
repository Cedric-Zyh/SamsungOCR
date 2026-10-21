from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess.number_overlap import prepare_overlapping_number
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict


def detect(text='(1)', confidence=.99, points=None):
    return {'text':text, 'confidence':confidence,
            'points': points or [[32, 12], [56, 12], [56, 33], [32, 33]]}


def run(tmp_path, boxes):
    image=np.full((300,400,3),255,np.uint8)
    image[197:218,188:212]=(30,30,255)
    source=tmp_path/'source.png';cv2.imwrite(str(source),image)
    destination=tmp_path/'number.png'
    def detector(path, **kwargs):
        search=cv2.imread(str(path))
        assert search.shape[:2] == (37,88)
        return boxes
    result=prepare_overlapping_number(source,destination,[100,150,300,200],
                                      reader=SimpleNamespace(detect_boxes=detector),provider='paddle_v6')
    return result,destination,image


def test_number_overlapping_type_box_is_automatically_found(tmp_path):
    result,path,image=run(tmp_path,[detect()])
    assert result['status']=='detected' and result['text']=='(1)'
    l,t,r,b=result['box']
    assert t<200<b
    np.testing.assert_array_equal(cv2.imread(str(path)),image[t:b,l:r])


@pytest.mark.parametrize('boxes',[
    [detect('1')], [detect('(I)')], [detect('(1234)')], [detect(confidence=.4)],
    [detect(),detect('(2)')], [detect(points=[[0,12],[24,12],[24,33],[0,33]])],
    [detect(points=[[32,16],[56,16],[56,36],[32,36]])],
])
def test_ambiguous_or_truncated_or_unbracketed_content_is_not_a_short_number(tmp_path,boxes):
    result,path,_=run(tmp_path,boxes)
    assert result is None and not path.exists()


BASE='北京华康君泰贸易有限公司售后业务专用章'
@pytest.mark.parametrize('required,actual,status',[
    (BASE,BASE+'(1)','匹配'),
    (BASE,BASE+'（２）','匹配'),
    (BASE+'(1)',BASE+'(1)','匹配'),
    (BASE+'(2)',BASE+'(1)','不匹配'),
    (BASE+'(1)',BASE,'部分匹配'),
    (BASE,BASE+'11011110146728','不匹配'),
    (BASE,BASE+'(1234)','不匹配'),
    (BASE,BASE.replace('君泰','君太')+'(1)','不匹配'),
    (BASE+'6237143站',BASE+'(1)6237144站','不匹配'),
    (BASE+'6237143站',BASE+'(1)6237143站','匹配'),
])
def test_optional_number_does_not_hide_required_identity_fields(required,actual,status):
    result=compare_seal_text_strict(required,[actual],mismatch_over_partial=True)
    assert result['status']==status
    assert result['recognized']==actual
