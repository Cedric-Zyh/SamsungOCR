"""New real line inputs stay inside the existing OCR accounting boundary."""
from PIL import Image

from receipt_ocr.providers import paddle_runtime
from receipt_ocr.recognition.seal.ocr.interface import OcrReader
from receipt_ocr.recognition.seal.ocr.receiving import read_input, input_item
from receipt_ocr.runtime.calls import capture_recognition_calls
from receipt_ocr.runtime.progress import model_stage


def test_receiving_actual_inputs_are_counted_and_failures_never_supply_text(monkeypatch, tmp_path):
    source=tmp_path/'source.png'
    Image.new('RGB',(120,50),'white').save(source)
    state={'error':False}
    class Predictor:
        def predict(self, **_):
            if state['error']:
                raise RuntimeError('prediction failed')
            return [{'rec_text':'仓库021DC0','rec_score':.98}]
    monkeypatch.setattr(paddle_runtime,'_line_recognizer',lambda _:Predictor())
    artifact={'index':0,'region_id':'seal-0','inputs':[],'reads':[],'errors':[]}
    with capture_recognition_calls(source,tmp_path,'/files/artifacts/test') as captured, model_stage('seal','paddle_v6'):
        first=input_item(artifact,'receiving-one','/files/artifacts/test/source.png','完整代码段','receiving_split')
        good=read_input(artifact,OcrReader(),'paddle_v6',source,first)
        state['error']=True
        second=input_item(artifact,'receiving-two','/files/artifacts/test/source.png','另一真实图','receiving_split')
        failed=read_input(artifact,OcrReader(),'paddle_v6',source,second)
    snapshot=captured.snapshot()
    assert snapshot['summary']['total']==2
    assert snapshot['summary']['succeeded']==1 and snapshot['summary']['failed']==1
    assert all(row['stage']=='seal' and row['image_url'].endswith('/source.png') for row in snapshot['calls'])
    assert good['text']=='仓库021DC0' and first['status']=='completed'
    assert failed['text']=='' and failed['confidence']==0 and second['status']=='failed'
    assert len(artifact['reads'])==1 and artifact['reads'][0]['matching'] is False
    assert len(artifact['errors'])==1
