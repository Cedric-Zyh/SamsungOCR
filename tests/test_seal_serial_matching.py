import pytest

from receipt_ocr.domain.seal_serial import serial_matching_texts
from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict

COMPANY = '湖南和联电子科技有限公司'
CODE = '4301020263035'


def stamp(code=CODE, company=COMPANY, index=0):
    return dict(original_url=f'/original-{index}.png', shape='圆形',
                unwrapped_text=company, ring_serial_text=code,
                ring_serial_detection={'status': 'recognized' if code else 'unreadable'},
                reads=[{'channel': 'serial', 'text': code}])


def candidates(requirement, artifacts):
    return seal_candidates({'fields': {'签章要求': requirement},
                            'processing_artifacts': {'seals': artifacts}})


@pytest.mark.parametrize('code', [CODE, '1111111111111', ''])
def test_unrequested_serial_does_not_affect_company_match(code):
    result = candidates(COMPANY, [stamp(code)])[0]
    assert result['text_status'] == '匹配'
    assert result['recognized'] == COMPANY
    assert serial_matching_texts(COMPANY, stamp(code)) == []


@pytest.mark.parametrize('suffix', [CODE, f'（{CODE}）', f' 编号：{CODE}', f'印章编号{CODE}', f'章号:{CODE}'])
@pytest.mark.parametrize('actual', [CODE, '4301020263036', '430102026303', '43010202630355', ''])
def test_required_serial_must_be_complete_and_exact(suffix, actual):
    requirement = COMPANY + suffix
    result = candidates(requirement, [stamp(actual)])[0]
    assert (result['text_status'] == '匹配') == (actual == CODE)
    combined = serial_matching_texts(requirement, stamp(actual))
    comparison = compare_seal_text_strict(requirement, [COMPANY, *combined])
    assert (comparison['status'] == '匹配') == (actual == CODE)


def test_never_join_company_and_code_across_stamps():
    rows = candidates(COMPANY + CODE, [stamp('1111111111111'), stamp(CODE, '其他有限公司', 1)])
    assert all(row['text_status'] != '匹配' for row in rows)


def test_centre_number_is_still_required():
    artifact = stamp()
    artifact['round_type_band_text'] = '收货专用章(2)'
    requirement = COMPANY + '收货专用章(1)'
    assert candidates(requirement, [artifact])[0]['text_status'] != '匹配'
    artifact['round_type_band_text'] = '收货专用章(1)'
    assert candidates(requirement, [artifact])[0]['text_status'] == '匹配'
    assert candidates(requirement + '编号' + CODE, [artifact])[0]['text_status'] == '匹配'


def test_station_number_is_not_removed():
    assert compare_seal_text_strict('三星售后6183342站', ['三星售后6183343站'])['status'] != '匹配'
    assert serial_matching_texts('三星售后6183342站', stamp()) == []
