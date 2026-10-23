from copy import deepcopy

import pytest

from receipt_ocr.application.assembly import select_variants, merge_primary
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.postprocess.channels import record_local_channel
from receipt_ocr.recognition.seal.postprocess.providers import combine_seal_provider_checks
from receipt_ocr.recognition.seal.providers.qingtong import compare_qingtong_seal


REQUIRED = '北京尚亿通科技有限公司西城分公司售后服务专用章'
PARTIAL = REQUIRED.replace('分', '', 1)
WRONG = REQUIRED.replace('后', '活')


def remote(text=WRONG):
    return compare_qingtong_seal(REQUIRED, {'ok': True, 'response': {'data': {'img_0': [{
        'matched_seal': {'label': '无关公司的收货章'}, 'text_formatted': text,
        'xyxy': [1, 2, 30, 40]}]}}})


def local(text, region=False):
    check = compare_seal_text_strict(REQUIRED, [text])
    check.update(backend='PaddleOCR PP-OCRv6 Small', recognition_mode='local')
    if region:
        check['region_source'] = '清瞳印章区域'
    return check


@pytest.mark.parametrize('in_remote_region', [False, True])
@pytest.mark.parametrize('text,status', [(PARTIAL, '部分匹配'), (REQUIRED + '多', '不匹配')])
def test_local_partial_and_better_mismatch_participate_independently(in_remote_region, text, status):
    qt = remote(WRONG if status == '部分匹配' else '无关客户章')
    paddle = local(text, in_remote_region)
    original = deepcopy([qt, paddle])
    result = record_local_channel(qt, paddle)
    assert result['recognized'] == result['display_text'] == text
    assert result['status'] == status
    assert result['source'] == paddle['backend']
    assert result['dual_check'] == qt['dual_check']
    assert result['qingtong_channel']['recognized'] == qt['recognized']
    assert result['local_channel']['recognized'] == text
    assert [qt, paddle] == original


def test_better_remote_result_stays_selected_without_losing_local_evidence():
    result = record_local_channel(remote(REQUIRED), local(PARTIAL))
    assert result['status'] == '匹配'
    assert result['source'] == '清瞳 · 印章文字 OCR'
    assert result['local_channel']['status'] == '部分匹配'


def test_simulated_local_match_cannot_replace_real_mismatch():
    paddle = local(REQUIRED)
    paddle.update(simulated=True, reliable=False)
    result = record_local_channel(remote(), paddle)
    assert result['recognized'] == WRONG
    assert result['status'] == '不匹配'


def test_all_mode_checks_local_independently_while_displaying_best_text():
    result = record_local_channel(remote(REQUIRED), local(PARTIAL), 'all')
    assert result['recognized'] == REQUIRED
    assert result['status'] == '部分匹配'
    assert result['reliable'] is False
    assert '尚未全部匹配' in result['message']


def test_nested_local_is_not_relabelled_as_a_remote_vote_when_combining_again():
    attached = record_local_channel(remote(), local(PARTIAL))
    result = combine_seal_provider_checks([attached, local(PARTIAL)])
    assert result['recognized'] == PARTIAL
    assert len(result['provider_comparison']['sources']) == 2
    qt, paddle = result['provider_comparison']['sources']
    assert qt['recognized'] == WRONG
    assert paddle['recognized'] == PARTIAL
    assert '清瞳' in qt['source']
    assert 'Paddle' in paddle['source']


def test_separate_local_variant_participates_in_final_stage_assembly():
    variants = [{'method': 'qingtong', 'result': {'seal_check': remote()}},
                {'method': 'paddle_v6', 'result': {'seal_check': local(PARTIAL)}}]
    acceptance = {'seal_match_mode': 'any'}
    combined, decisions, primary = select_variants('seal', variants, acceptance)
    assert decisions == variants
    output = {'processing_artifacts': {}}
    merge_primary(output, 'seal', primary, variants, variants, combined, acceptance)
    assert output['seal_check']['recognized'] == PARTIAL
    assert output['seal_check']['status'] == '部分匹配'


def test_inline_local_read_before_backend_metadata_has_an_explicit_source():
    paddle = compare_seal_text_strict(REQUIRED, [PARTIAL])
    result = record_local_channel(remote(), paddle)
    assert result['recognized'] == PARTIAL
    assert result['source'] == '本地印章 OCR'


def test_all_mode_cannot_pass_when_another_configured_local_variant_failed():
    successful = [{'method': 'paddle_v6', 'result': {'seal_check': local(REQUIRED)}}]
    variants = successful + [{'method': 'paddle_seal', 'error': '模型调用失败'}]
    acceptance = {'seal_match_mode': 'all'}
    combined, _, primary = select_variants('seal', successful, acceptance)
    output = {'processing_artifacts': {}}
    merge_primary(output, 'seal', primary, successful, variants, combined, acceptance)
    assert output['seal_check']['status'] == '需人工复核'
    assert output['seal_check']['reliable'] is False
