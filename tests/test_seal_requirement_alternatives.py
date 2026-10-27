import pytest

from receipt_ocr.domain.seal_name_terms import seal_name_terms
from receipt_ocr.domain.parsing.parsing_seals import compare_seal_text_strict
from receipt_ocr.recognition.seal.postprocess.name_correction import registered_company_names

OWNER='青岛和沃电子技术有限公司'
FIRST=OWNER+'业务专用章'
SECOND=OWNER+'维修业务专用章（1）'
REQUIREMENT=FIRST+'/'+SECOND


@pytest.mark.parametrize('separator',['/','／',' / '])
def test_full_wordings_share_owner_without_sharing_purpose_or_number(separator):
    text=FIRST+separator+SECOND
    terms=seal_name_terms(text)
    assert terms['original']==text and terms['company']==OWNER
    assert terms['parse_status']=='parsed' and terms['groupable']
    assert terms['purpose']==terms['number']==''
    assert [(a['purpose'],a['number']) for a in terms['alternatives']]==[
        ('业务专用章',''),('维修业务专用章','(1)')]
    assert registered_company_names([{'requirement':text}])==(OWNER,)


@pytest.mark.parametrize('recognized,index',[(FIRST,0),(SECOND,1)])
def test_each_complete_observed_wording_matches_with_traceability(recognized,index):
    result=compare_seal_text_strict(REQUIREMENT,[recognized])
    assert result['status']=='匹配' and result['requirement']==REQUIREMENT
    assert result['field_comparison']['matched_requirement']==[FIRST,SECOND][index]
    assert result['field_comparison']['allowed_requirements']==[FIRST,SECOND]


@pytest.mark.parametrize('recognized',[
    OWNER+'维修业务专用章（2）', OWNER+'维修业务专用章',
    '青岛其他电子技术有限公司业务专用章',OWNER+'财务专用章（1）',
])
def test_wrong_number_missing_number_or_different_owner_and_purpose_never_match(recognized):
    assert compare_seal_text_strict(REQUIREMENT,[recognized])['status']!='匹配'


def test_separate_observations_never_supply_each_others_missing_fields():
    texts=[OWNER, '业务专用章','维修业务专用章（1）']
    assert compare_seal_text_strict(REQUIREMENT,texts)['status']!='匹配'


@pytest.mark.parametrize('text',[
    '广州花东仓020DCH收货专用章①/②',
    '甲乙电子有限公司收货专用章020/ABC',
    '甲乙电子有限公司业务专用章电话:010-12345678/13800138000',
    '甲乙(A/B)有限公司业务专用章',
    '甲乙电子有限公司业务专用章/丙丁电子有限公司业务专用章（1）',
    '业务专用章/甲乙电子有限公司维修业务专用章（1）',
    '甲乙电子有限公司北京分公司业务专用章/甲乙电子有限公司上海分公司业务专用章',
])
def test_codes_numbers_contacts_other_owners_and_incomplete_options_are_not_split(text):
    assert not seal_name_terms(text).get('alternatives')


def test_number_choices_inside_full_wordings_keep_their_local_scope():
    first='广州花东仓020DCH收货专用章①/②'
    second='广州花东仓020DCH业务专用章（3）'
    text=first+'/'+second
    terms=seal_name_terms(text)
    assert len(terms['alternatives'])==2
    assert terms['alternatives'][0]['number']=='①/②'
    assert terms['alternatives'][1]['number']=='(3)'
    assert compare_seal_text_strict(text,['广州花东仓020DCH收货专用章①'])['status']=='匹配'
    assert compare_seal_text_strict(text,['广州花东仓020DCH业务专用章（2）'])['status']!='匹配'


@pytest.mark.parametrize('text,matched',[(FIRST,True),(SECOND,True),
    (OWNER+'维修业务专用章（2）',False)])
def test_standard_check_uses_the_actual_complete_wording_and_shape(text,matched):
    from test_seal_standards import receipt,artifact,standard
    from receipt_ocr.domain.seal_standards import apply_standard_check
    item=receipt(artifact(text=text))
    item['fields']['签章要求']=REQUIREMENT
    reference={**standard(),'requirement':REQUIREMENT}
    result=apply_standard_check(item,reference)
    assert (result['seal_standard_check']['status']=='matched') is matched
