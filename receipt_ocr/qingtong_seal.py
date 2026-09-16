"""Compare trained seal identity and OCR separately, within each detected seal."""
from math import isfinite

from .parsing_seals import compare_seal_text_strict, normalize_seal_text_strict, seal_text_comparison_rank
from .parsing_text import normalize_text



def _seals(data):
    if isinstance(data, list):
        for item in data:
            yield from _seals(item)
    elif isinstance(data, dict):
        if any(key in data for key in ('matched_seal', 'text_formatted', 'xyxy')):
            yield data
        else:
            for value in data.values():
                yield from _seals(value)


def compare_qingtong_seal(requirement, external, match_mode="any"):
    candidates = []
    excluded_candidates = []
    payload = external.get('response')
    data = payload.get('data', {}) if isinstance(payload, dict) else {}
    for index, seal in enumerate(_seals(data)):
        template = seal.get('matched_seal')
        template = template if isinstance(template, dict) else {}
        label = template.get('label') if isinstance(template.get('label'), str) else ''
        text = seal.get('text_formatted') if isinstance(seal.get('text_formatted'), str) else ''
        # Dispatch stamps cannot supply evidence for the customer's receipt seal.
        if any('商品付讫章' in normalize_text(value) for value in (label, text)):
            excluded_candidates.append({
                'index': index, 'xyxy': seal.get('xyxy'),
                'template_label': label, 'ocr_text': text,
                'reason': '商品付讫章不参与客户印章比对',
            })
            continue
        try:
            similarity = float(template.get('similarity'))
            if not isfinite(similarity) or not 0 <= similarity <= 1:
                similarity = None
        except (TypeError, ValueError):
            similarity = None
        template_check = compare_seal_text_strict(requirement, [label] if label else [])
        ocr_check = compare_seal_text_strict(requirement, [text] if text else [])
        template_matches = bool(template_check['status'] == '匹配' and template_check['reliable'])
        ocr_matches = bool(ocr_check['status'] == '匹配' and ocr_check['reliable'])
        complete = bool(normalize_seal_text_strict(label) and normalize_seal_text_strict(text))
        count = int(template_matches) + int(ocr_matches)
        best_channel = 'template' if seal_text_comparison_rank(template_check) > seal_text_comparison_rank(ocr_check) else 'ocr'
        comparison = template_check if best_channel == 'template' else ocr_check
        candidates.append({
            'index': index, 'xyxy': seal.get('xyxy'), 'match_count': count, 'complete': complete,
            'best_channel': best_channel, 'comparison': comparison, 'status': comparison['status'],
            'template': {'matched': template_matches, 'status': template_check['status'], 'label': label, 'similarity': similarity,
                         'requirement_match': template_check},
            'ocr': {'matched': ocr_matches, 'status': ocr_check['status'], 'text': text, 'comparison': ocr_check,
                    'api_text_similarity': seal.get('text_similarity')},
        })
    selected = max(candidates, key=lambda c: (seal_text_comparison_rank(c['comparison']), c['match_count']), default=None)
    result = compare_seal_text_strict(requirement, [])
    match_mode = 'all' if match_mode == 'all' else 'any'
    result.update(dual_check={'policy': f'qingtong_{match_mode}_channel', 'selected': selected,
                             'candidates': candidates, 'excluded_candidates': excluded_candidates},
                  api=external, recognition_mode='qingtong_only', backend='清瞳印章 API')
    if not external.get('ok'):
        result.update(status='识别失败', reliable=False, message=external.get('message') or '清瞳接口请求失败')
        return result
    if not normalize_seal_text_strict(requirement):
        result.update(status='缺少比对依据', reliable=False, message='未提供签章要求，无法比较两路印章结果')
        return result
    if selected is None:
        message = ('已排除商品付讫章，未识别到可用于客户印章核对的印章'
                   if excluded_candidates else '清瞳未返回有效印章结果')
        result.update(status='未识别', reliable=False, message=message)
        return result
    comparison = selected['comparison']
    all_recognized = list(dict.fromkeys(
        text for candidate in candidates
        for text in (candidate['ocr']['text'], candidate['template']['label'])
        if normalize_seal_text_strict(text)
    ))
    result.update(recognized=comparison['recognized'], all_recognized=all_recognized,
                  score=comparison['score'], confidence=comparison['confidence'],
                  requirement_coverage=comparison['requirement_coverage'],
                  source='清瞳 · 印章模板识别' if selected['best_channel'] == 'template' else '清瞳 · 印章文字 OCR')
    count = selected['match_count']
    channel_match = count == 2 if match_mode == 'all' else count > 0
    status = '匹配' if channel_match else '部分匹配' if (comparison['status'] == '部分匹配' or (match_mode == 'all' and count == 1)) else '不匹配'
    message = ('同一印章的模板识别与文字 OCR 均符合签章要求' if count == 2 else
               '印章模板识别与签章要求完整一致，判定匹配' if selected['template']['matched'] else
               '印章文字 OCR 与签章要求完整一致，判定匹配' if count == 1 else
               '印章识别文字存在漏字，部分匹配，需人工复核' if status == '部分匹配' else
               '印章模板识别与文字 OCR 均未匹配签章要求')
    # Missing evidence must not be interpreted as a confirmed negative.
    reliable = channel_match or (status == '不匹配' and selected['complete'])
    if match_mode == 'all' and count == 1:
        message = '仅有一路印章结果匹配，全部结果匹配模式下需人工复核'
    if status == '不匹配' and not selected['complete']:
        message += '；接口缺少完整的两路结果，需人工复核'
    result.update(status=status, reliable=reliable, message=message)
    return result
