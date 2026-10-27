"""Same-row real crop reads may complete, never invent, a circled number."""
from copy import deepcopy
from pathlib import Path

import pytest
from PIL import Image

from receipt_ocr.domain.ocr import TextObservation
from receipt_ocr.domain.seal_standards import seal_candidates
from receipt_ocr.recognition.seal.ocr import receiving_number as module

TITLE = '广州花东仓020DCH'
PURPOSE = '收货专用章'


def fixture(monkeypatch, tmp_path, outputs=(('②', .76), ('②', .96))):
    urls=[]
    for index in range(3):
        name=f'row-{index}.png'
        Image.new('RGB', (400,140),'white').save(tmp_path/name)
        urls.append('/files/test/'+name)
    artifact={'index':1,'region_id':'seal-1','shape_code':'rectangle','color':'red',
      'original_url':'/files/test/original.png','color_isolated_url':'/files/test/color.png',
      'rectangular_layout':'frame_rows','rectangular_row_urls':urls,
      'rectangular_fixed_rows':[{'coordinate_space':'oriented','crop_box':[0,i*100,360,(i+1)*100]} for i in range(3)],
      'rectangular_row_texts':[TITLE,PURPOSE,''], 'rectangular_text':TITLE+PURPOSE,
      'inputs':[],'errors':[],
      'reads':[{'id':'title','channel':'body','text':TITLE,'confidence':.99,'rectangle_row_index':0},
               {'id':'purpose','channel':'body','text':PURPOSE,'confidence':.99,'rectangle_row_index':1}]}
    prepared=[]
    def prepare(source,directory,index,prefix,*,row_index,purpose_right):
        prepared.append((source,row_index,purpose_right))
        jobs=[]
        for scale in (1,2):
            path=tmp_path/f'number-{scale}.png';Image.new('RGB',(100,100),'white').save(path)
            item={'id':f'seal-{index}:number-{scale}','channel':'receiving_number',
                  'rectangle_row_index':row_index,'number_scale':scale,'representation':'color',
                  'label':f'圈号{scale}','image_url':f'{prefix}/{path.name}','matching':False}
            jobs.append((item,path))
        return {'prepared':True,'inputs':[item for item,_ in jobs]},jobs
    monkeypatch.setattr(module,'prepare_receiving_number',prepare)
    class Reader:
        calls=[]
        def read_line(self,path,**kwargs):
            text,confidence=outputs[len(self.calls)];self.calls.append(Path(path).name)
            return [TextObservation(text,confidence,0,0,1,1)] if text else []
    return artifact,Reader(),prepared


def run(artifact,reader,tmp_path):
    return module.read_receiving_number(artifact,tmp_path,'/files/test','paddle_v6',reader)


def test_matching_literal_circle_views_complete_one_stamp_and_retain_raw_evidence(monkeypatch,tmp_path):
    artifact,reader,prepared=fixture(monkeypatch,tmp_path)
    raw=deepcopy(artifact['reads'])
    candidate=run(artifact,reader,tmp_path)
    assert candidate==TITLE+PURPOSE+'②'
    assert reader.calls==['number-1.png','number-2.png'] and prepared[0][1]==1
    assert artifact['reads'][:2]==raw
    assert all(read['matching'] is False for read in artifact['reads'][2:])
    assert artifact['rectangular_row_texts'][1]==PURPOSE+'②'
    assert artifact['rectangular_text_before_receiving_number']==TITLE+PURPOSE
    assert artifact['receiving_number']['status']=='adopted'
    assert artifact['matching_texts']==[candidate]
    result={'fields':{'签章要求':TITLE+PURPOSE+'①/②'},'processing_artifacts':{'seals':[artifact]}}
    assert seal_candidates(result)[0]['text_status']=='匹配'
    result['fields']['签章要求']=TITLE+PURPOSE+'①'
    assert seal_candidates(result)[0]['text_status']=='不匹配'


@pytest.mark.parametrize('outputs', [
    (('2',.99),('2',.99)), (('(2)',.99),('(2)',.99)),
    (('②',.99),('③',.99)), (('②',.74),('②',.99)),
    (('',0),('②',.99)), (('②',.99),('',0)),
    (('0',.33),('0',.38)), (('②③',.99),('②③',.99)),
])
def test_uncertain_non_circle_or_disagreeing_views_never_append_number(monkeypatch,tmp_path,outputs):
    artifact,reader,_=fixture(monkeypatch,tmp_path,outputs)
    assert run(artifact,reader,tmp_path) is None
    assert artifact['rectangular_text']==TITLE+PURPOSE
    assert artifact['receiving_number']['status']==('conflict' if outputs[0][0]=='②' and outputs[1][0]=='③' else 'unverified')
    assert not artifact.get('matching_texts')
    assert len(reader.calls)==2


@pytest.mark.parametrize('token',['①','③'])
def test_other_actual_numbers_are_preserved_instead_of_filled_from_requirement(monkeypatch,tmp_path,token):
    artifact,reader,_=fixture(monkeypatch,tmp_path,((token,.99),(token,.99)))
    assert run(artifact,reader,tmp_path)==TITLE+PURPOSE+token
    assert artifact['receiving_number']['number_text']==token


@pytest.mark.parametrize('other',[PURPOSE+'①',PURPOSE+'③','①','收费专用章'])
def test_existing_reliable_same_row_number_or_purpose_conflict_blocks_merge(monkeypatch,tmp_path,other):
    artifact,reader,_=fixture(monkeypatch,tmp_path)
    artifact['reads'].append({'channel':'receiving_detection','text':other,'confidence':.99,'rectangle_row_index':1})
    assert run(artifact,reader,tmp_path) is None
    assert artifact['receiving_number']['status']=='conflict'
    assert artifact['rectangular_text']==TITLE+PURPOSE


@pytest.mark.parametrize('status',['conflict','insufficient'])
def test_managed_withheld_identity_cannot_be_resurrected_by_a_circle_read(monkeypatch,tmp_path,status):
    artifact,reader,_=fixture(monkeypatch,tmp_path)
    artifact['receiving_form']={'managed':True,'status':status,'identity_text':'',
                               'candidate_text':TITLE+PURPOSE,'primary':{'purpose_row_index':1}}
    artifact['matching_texts']=[]
    assert run(artifact,reader,tmp_path) is None
    assert artifact['matching_texts']==[]
    assert artifact['receiving_form']['status']==status
    result={'fields':{'签章要求':TITLE+PURPOSE+'②'},'processing_artifacts':{'seals':[artifact]}}
    assert seal_candidates(result)[0]['texts']==[]


def test_adopted_same_row_primary_identity_is_completed_without_editing_primary_reads(monkeypatch,tmp_path):
    artifact,reader,_=fixture(monkeypatch,tmp_path)
    artifact['receiving_form']={'managed':True,'status':'adopted','selected_source':'primary',
      'identity_text':TITLE+PURPOSE,'primary':{'purpose_row_index':1,'identity_text':TITLE+PURPOSE}}
    artifact['matching_texts']=[TITLE+PURPOSE]
    before=deepcopy(artifact['receiving_form']['primary'])
    assert run(artifact,reader,tmp_path)==TITLE+PURPOSE+'②'
    assert artifact['matching_texts']==[TITLE+PURPOSE+'②']
    assert artifact['receiving_form']['primary']==before


@pytest.mark.parametrize('change',['row','source','active'])
def test_selected_identity_requires_its_original_physical_row_mapping(monkeypatch,tmp_path,change):
    artifact,reader,_=fixture(monkeypatch,tmp_path)
    artifact['receiving_form']={'managed':True,'status':'adopted','selected_source':'primary',
      'identity_text':TITLE+PURPOSE,'primary':{'purpose_row_index':1}}
    artifact['matching_texts']=[TITLE+PURPOSE]
    if change=='row':artifact['receiving_form']['primary']['purpose_row_index']=2
    if change=='source':artifact['receiving_form']['selected_source']='verified_grid_recovery'
    if change=='active':artifact['matching_texts']=[]
    assert run(artifact,reader,tmp_path) is None
    assert artifact['receiving_form']['identity_text']==TITLE+PURPOSE


@pytest.mark.parametrize('wrong',['region_id','stamp_index'])
def test_number_evidence_cannot_be_borrowed_from_a_different_stamp(monkeypatch,tmp_path,wrong):
    artifact,reader,_=fixture(monkeypatch,tmp_path)
    assert run(artifact,reader,tmp_path)
    evidence=deepcopy(artifact['receiving_number']);evidence[wrong]='another'
    original=artifact['rectangular_text']
    assert module.adopt_receiving_number(artifact,evidence) is None
    assert artifact['rectangular_text']==original


def test_two_real_input_calls_are_accounted_to_the_seal_stage(monkeypatch,tmp_path):
    from receipt_ocr.providers import paddle_runtime
    from receipt_ocr.recognition.seal.ocr.interface import OcrReader
    from receipt_ocr.runtime.calls import capture_recognition_calls
    from receipt_ocr.runtime.progress import model_stage
    artifact,_,_=fixture(monkeypatch,tmp_path)
    class Predictor:
        def predict(self,**kwargs):
            return [{'rec_text':'②','rec_score':.96}]
    monkeypatch.setattr(paddle_runtime,'_line_recognizer',lambda _:Predictor())
    with capture_recognition_calls(tmp_path/'row-1.png',tmp_path,'/files/artifacts/test') as captured, model_stage('seal','paddle_v6'):
        assert run(artifact,OcrReader(),tmp_path)==TITLE+PURPOSE+'②'
    snapshot=captured.snapshot()
    assert snapshot['summary']['total']==2 and snapshot['summary']['succeeded']==2
    assert all(call['stage']=='seal' and call['image_url'].endswith(('.png',)) for call in snapshot['calls'])


@pytest.mark.parametrize('outputs',[(('②',.99),('②',.99)),(('②',.99),('③',.99))])
def test_pipeline_withholds_former_exact_candidate_when_new_circle_evidence_conflicts(monkeypatch,tmp_path,outputs):
    from receipt_ocr.recognition.seal import pipeline
    directory=tmp_path/'seals';directory.mkdir()
    artifact,reader,_=fixture(monkeypatch,directory,outputs)
    artifact['reads'].append({'channel':'body','text':'③','confidence':.99,'rectangle_row_index':1})
    artifact['rectangular_row_texts'][1]+= '③'
    artifact['rectangular_text']+= '③'
    monkeypatch.setattr(pipeline,'OcrReader',lambda:reader)
    monkeypatch.setattr(pipeline,'prepare_inputs',lambda *args:(deepcopy(artifact),[]))
    monkeypatch.setattr(pipeline,'read_double_border',lambda *args,**kwargs:False)
    monkeypatch.setattr(pipeline,'read_continuous_company',lambda *args,**kwargs:False)
    monkeypatch.setattr(pipeline,'finalize_multi_zone',lambda *args:[])
    monkeypatch.setattr(pipeline,'merge_number_read',lambda *args:None)
    monkeypatch.setattr(pipeline,'serial_matching_texts',lambda *args:[])
    monkeypatch.setattr(pipeline,'correct_local_seal_names',lambda a,texts:texts)
    monkeypatch.setattr(pipeline,'recognize_receiving_form',lambda *args,**kwargs:None)
    texts,artifacts=pipeline._recognize_local_seals(tmp_path/'source.png',[],[object()],tmp_path,
        '/files/test','paddle_v6',requirement=TITLE+PURPOSE+'③',orientation_mode='combined')
    assert texts==[]
    assert artifacts[0]['receiving_number']['status']=='conflict'
    assert artifacts[0]['rectangular_text']==TITLE+PURPOSE+'③'
    assert any(read.get('text')=='③' for read in artifacts[0]['reads'])
