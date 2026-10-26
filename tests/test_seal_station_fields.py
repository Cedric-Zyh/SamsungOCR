import pytest

from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.domain.seal_standards import standard_comparison
from receipt_ocr.runtime.safety import _apply_single_paddle_safety


REQUIRED = '三星电子服务中心上海信站代码6237106'
STAMP = '三星电子服务中心站代码上海信威6237106TEL:021-51035898上海市南京西路819号中创大區210€'


def compare(text=STAMP, requirement=REQUIRED):
    return compare_seal_text_strict(requirement, [text], mismatch_over_partial=True)


@pytest.mark.parametrize('text', [
    STAMP,
    STAMP.replace('站代码上海信威', '上海信威站代码'),
    STAMP.replace('TEL:', '电话：'),
    STAMP.replace('TEL:', 'tel'),
    STAMP.replace('6237106', '６２３７１０６'),
    STAMP.replace('站代码上海信威', '上海信威：站代码'),
    STAMP.split('TEL:')[0],
    STAMP.replace('上海信威', '上海信'),
])
def test_station_identity_matches_independently_of_layout_and_extra_contacts(text):
    result = compare(text)
    assert result['status'] == '匹配'
    assert result['recognized'] == text
    assert result['all_recognized'] == [text]
    assert result['comparison_policy'] == 'strict_information_fields'
    assert result['field_comparison']['recognized_fields']['站代码'] == '6237106'


def test_short_printed_name_is_not_silently_rewritten():
    result = compare()
    fields = result['field_comparison']
    assert fields['matched_fields'] == ['服务中心名称', '站代码']
    assert fields['additional_fields'] == ['电话', '地址']
    assert fields['name_differences'] == [
        {'field': '站点名称', 'required': '上海信', 'recognized': '上海信威'},
    ]
    assert '上海信' in result['message'] and '上海信威' in result['message']
    assert result['requirement'] == REQUIRED


@pytest.mark.parametrize('text', [
    STAMP.replace('6237106', '6237107'),
    STAMP.replace('6237106', '623710'),
    STAMP.replace('6237106', '62371060'),
    STAMP.replace('6237106', '06237106'),
    STAMP.replace('6237106', '62371O6'),
    STAMP.replace('6237106', ''),
    STAMP.replace('站代码', ''),
    STAMP.replace('三星电子服务中心', '其他电子服务中心'),
    STAMP.replace('三星电子服务中心', ''),
    STAMP.replace('上海信威', '北京信威'),
    STAMP.replace('上海信威', '上海新威'),
    STAMP.replace('上海信威', '上海信威分公司'),
    STAMP.replace('上海信威', ''),
    STAMP + '站代码6237107',
    STAMP + '其他公司',
    STAMP + 'TEL:021-00000000',
    STAMP.replace('TEL:', ''),
    STAMP.replace('021-51035898', '021-510358980'),
])
def test_conflicting_codes_names_and_ambiguous_contact_tails_do_not_pass(text):
    assert compare(text)['status'] != '匹配'


@pytest.mark.parametrize('requirement', [
    REQUIRED + '电话:021-51035899',
    REQUIRED + '地址:上海市南京西路820号',
    REQUIRED + '收货专用章(1)',
    REQUIRED.replace('上海信', '上海信威寮'),
])
def test_extra_required_information_cannot_be_ignored(requirement):
    assert compare(requirement=requirement)['status'] != '匹配'


def test_separate_stamp_candidates_cannot_complete_each_other():
    result = compare_seal_text_strict(REQUIRED, ['三星电子服务中心', '上海信威站代码6237106'])
    assert result['status'] != '匹配'


def test_local_safety_and_standard_shape_checks_use_the_same_station_evidence():
    check = compare()
    _apply_single_paddle_safety({}, check, {'page': 'paddle_v6', 'date': 'paddle_v6', 'seal': 'paddle_v6'})
    assert check['status'] == '匹配' and check['reliable'] is True
    receipt = {'fields': {'签章要求': REQUIRED}, 'processing_artifacts': {'seals': [{
        'index': 0, 'shape_code': 'rectangle', 'color': 'red',
        'original_url': '/stamp.png', 'color_isolated_url': '/clean.png', 'rectangular_text': STAMP,
    }]}}
    assert standard_comparison(receipt, {'shape': 'rectangle', 'color': 'red'})['status'] == 'matched'
    assert standard_comparison(receipt, {'shape': 'round', 'color': 'red'})['status'] == 'unmatched'
