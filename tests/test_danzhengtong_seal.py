"""Independent seal-scene contract tests; all remote responses are injected."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from receipt_ocr import danzhengtong as dzt
from receipt_ocr import danzhengtong_seal as seals
from receipt_ocr.application.context import DocumentContext
from receipt_ocr.application.plans import run_configured
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.runtime.calls import capture_recognition_calls


def credentials(**kwargs):
    return dzt.Settings(app_id='test-app', app_key='test-key', app_secret='test-secret',
                        key_id='test-gateway', callback_url='https://example.com/notify', **kwargs)


def commit(text='北京恒远恒信科技发展有限公司 收发货专用章', color='蓝色', shape='椭圆章'):
    return {name: {'value': value, 'ratio': .83, 'position': []} for name, value in {
        '收货客户印章': text, '印章颜色': color, '印章类型': shape,
    }.items()}


def png_bytes(color='navy', size=(13, 7)):
    image = Image.new('RGB', size, color)
    image.putpixel((0, 0), (12, 34, 56))
    image.putpixel((size[0] - 1, size[1] - 1), (90, 87, 65))
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


class ImageResponse:
    def __init__(self, content):
        self.content = content
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.content[:7]
        yield self.content[7:]

    def close(self):
        self.closed = True


class ImageTransport:
    def __init__(self, images=None, error=None):
        self.images = images or {}
        self.error = error
        self.calls = []
        self.responses = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        response = ImageResponse(self.images[url])
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


def completed(index=0, text='测试有限公司收货专用章', color='红色', shape='圆章', url=None, uuid='seal-uuid'):
    data = {'commitResult': commit(text, color, shape)}
    if url is not None:
        data['imgUrls'] = [url]
    return {'index': index, 'status': 'completed', 'req_uuid': uuid,
            'input_url': f'/files/input-{index}.png', 'original_url': f'/files/original-{index}.png',
            'color': 'red', 'result': data}


@pytest.mark.parametrize('color,ink,channel', [('red', (225, 55, 55), 0), ('blue', (55, 55, 225), 2)])
def test_prepare_preserves_complete_stamp_color_and_source_direction(tmp_path, monkeypatch, color, ink, channel):
    from receipt_ocr.recognition.seal.preprocess import inputs
    from receipt_ocr.recognition.seal.ocr.interface import OcrReader

    def forbidden(*args, **kwargs):
        raise AssertionError('the seal upload must not run local OCR or orientation')

    monkeypatch.setattr(inputs, 'prepare_inputs', forbidden)
    monkeypatch.setattr(inputs, 'prepare_round_stamp_combined', forbidden)
    monkeypatch.setattr(inputs, 'prepare_round_stamp_doc_ori', forbidden)
    monkeypatch.setattr(inputs, 'prepare_rectangular_stamp', forbidden)
    monkeypatch.setattr(OcrReader, 'read', forbidden)
    monkeypatch.setattr(OcrReader, 'read_line', forbidden)
    image = Image.new('RGB', (120, 80), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 20, 52), fill=ink)
    draw.rectangle((8, 42, 70, 52), fill=ink)
    draw.rectangle((90, 60, 98, 68), fill=ink)
    source = tmp_path / 'stamp.png'
    image.save(source)
    region = SealRegion(0, 0, 1, 1, color, '客户章', .1)
    monkeypatch.setattr(seals, 'detect_seal_regions', lambda *_: [region])
    result = seals.prepare_requests(source, tmp_path / 'artifacts', '/files/test')
    assert len(result) == 1
    item = result[0]
    assert item['local_orientation'] == 'not_run'
    assert item['region'] == region.to_dict()
    with Image.open(item['input_path']) as enhanced:
        output = np.asarray(enhanced.convert('RGB')).astype(np.int16)
        assert enhanced.width / enhanced.height == pytest.approx(1.5, abs=.003)
        others = [index for index in range(3) if index != channel]
        mask = output[:, :, channel] - output[:, :, others].max(axis=2) > 20
        original = np.asarray(image).astype(np.int16)
        original_mask = original[:, :, channel] - original[:, :, others].max(axis=2) > 20
        ys, xs = np.where(mask)
        original_ys, original_xs = np.where(original_mask)
        assert xs.mean() / enhanced.width == pytest.approx(original_xs.mean() / image.width, abs=.025)
        assert ys.mean() / enhanced.height == pytest.approx(original_ys.mean() / image.height, abs=.025)
        # The detached lower-right mark is part of the complete stamp too.
        assert mask[int(.75 * enhanced.height):int(.88 * enhanced.height),
                    int(.74 * enhanced.width):int(.84 * enhanced.width)].any()


def test_new_scene_settings_and_mapping_keep_page_model_and_provider_metadata():
    original = credentials(model_id='page-scene', seal_model_id='stamp-scene')
    selected = seals.seal_settings(original)
    assert selected.model_id == 'stamp-scene'
    assert original.model_id == 'page-scene'
    assert selected.app_secret == original.app_secret
    parsed = seals.normalize_seal_result(commit())
    assert parsed['fields'] == {
        '收货客户印章': '北京恒远恒信科技发展有限公司 收发货专用章',
        '印章颜色': '蓝色', '印章类型': '椭圆章',
    }
    assert parsed['metadata']['收货客户印章']['provider_ratio'] == .83
    assert 'confidence' not in parsed['metadata']['收货客户印章']
    assert seals.normalize_seal_result({'收货客户印章': {'value': 0}})['fields']['收货客户印章'] == '0'


@pytest.mark.parametrize('item', [False, 0, [], 'invalid', {'value': True}, {'value': []}])
def test_invalid_seal_fields_are_not_silently_treated_as_missing(item):
    with pytest.raises(ValueError):
        seals.normalize_seal_result({'收货客户印章': item})


def test_download_preserves_server_pixels_and_uses_valid_local_cache(tmp_path):
    url = 'https://images.example/rotated.png?signature=private'
    content = png_bytes()
    transport = ImageTransport({url: content})
    destination = tmp_path / 'returned.png'
    assert seals.download_returned_image(url, destination, transport=transport) == destination
    assert seals.download_returned_image(url, destination, transport=transport) == destination
    assert len(transport.calls) == 1
    assert set(transport.calls[0][1]) == {'stream', 'timeout'}
    assert transport.responses[0].closed is True
    with Image.open(destination) as actual, Image.open(BytesIO(content)) as expected:
        assert np.array_equal(np.asarray(actual), np.asarray(expected))


def test_disabled_and_deferred_images_keep_text_without_http(tmp_path, monkeypatch):
    transport = ImageTransport(error=AssertionError('image HTTP must not run'))
    monkeypatch.setattr(seals.requests, 'Session', lambda: transport)
    item = completed(url='https://images.example/stamp.png')
    monkeypatch.setattr(dzt, '_software_settings', credentials(download_seal_images=False))
    disabled = seals.build_stage_result(DocumentContext('unused.png'), {}, [item], artifact_dir=tmp_path)
    assert disabled['seal_check']['recognized'] == item['result']['commitResult']['收货客户印章']['value']
    assert disabled['processing_artifacts']['seals'][0]['returned_image_status'] == 'disabled'
    monkeypatch.setattr(dzt, '_software_settings', credentials(download_seal_images=True))
    deferred = seals.build_stage_result(DocumentContext('unused.png'), {}, [item], artifact_dir=tmp_path,
                                       download_mode='deferred')
    assert deferred['processing_artifacts']['seals'][0]['returned_image_status'] == 'pending'
    assert transport.calls == []


def test_disabled_images_still_show_existing_verified_cache(tmp_path, monkeypatch):
    item = completed(url='https://images.example/stamp.png')
    _, destination, _ = seals.returned_image_target(item, tmp_path, '/files/test')
    destination.parent.mkdir(parents=True)
    destination.write_bytes(png_bytes())
    monkeypatch.setattr(dzt, '_software_settings', credentials(download_seal_images=False))
    monkeypatch.setattr(seals, 'download_returned_image', lambda *_: pytest.fail('cache must avoid download'))
    result = seals.build_stage_result(DocumentContext('unused.png'), {}, [item], artifact_dir=tmp_path,
                                     artifact_url_prefix='/files/test')
    artifact = result['processing_artifacts']['seals'][0]
    assert artifact['returned_image_status'] == 'downloaded'
    assert artifact['returned_image_url'].endswith(destination.name)


def test_returned_artifact_uses_downloaded_server_image_and_keeps_each_stamp_separate(tmp_path, monkeypatch):
    urls = ['https://images.example/a.png', 'https://images.example/b.png?signature=private']
    transport = ImageTransport(dict(zip(urls, [png_bytes('red'), png_bytes('blue', (7, 13))])))
    monkeypatch.setattr(seals.requests, 'Session', lambda: transport)
    first = completed(0, '其他有限公司收货专用章', '红色', '圆章', urls[0], 'first')
    second = completed(1, '目标有限公司收货专用章', '蓝色', '椭圆章', urls[1], 'second')
    result = seals.build_stage_result(DocumentContext('unused.png'),
        {'签章要求': '目标有限公司收货专用章'}, [first, second], {'model_id': 'stamp-scene'},
        artifact_dir=tmp_path, artifact_url_prefix='/files/test')
    check = result['seal_check']
    assert check['status'] == '匹配'
    assert check['recognized'] == '目标有限公司收货专用章'
    assert check['provider_color'] == '蓝色'
    assert check['provider_seal_type'] == '椭圆章'
    assert len(check['candidates']) == 2
    artifacts = result['processing_artifacts']['seals']
    assert [item['provider_text'] for item in artifacts] == ['其他有限公司收货专用章', '目标有限公司收货专用章']
    assert artifacts[1]['color'] == 'blue'
    assert artifacts[1]['shape_code'] == 'ellipse'
    for artifact in artifacts:
        assert artifact['returned_image_status'] == 'downloaded'
        assert artifact['returned_image_url'] == artifact['body_url'] == artifact['color_isolated_oriented_url']
        assert artifact['returned_image_url'] != artifact['input_url']
        assert artifact['orientation']['local_rotation'] is False
        assert '?' not in artifact['returned_image_source_url']
    with Image.open(tmp_path / 'seals' / Path(artifacts[1]['body_url']).name) as actual:
        assert actual.size == (7, 13)


def test_new_request_at_same_stamp_index_cannot_reuse_old_server_image(tmp_path, monkeypatch):
    old_url, new_url = 'https://images.example/old.png', 'https://images.example/new.png'
    transport = ImageTransport({old_url: png_bytes('red'), new_url: png_bytes('blue')})
    monkeypatch.setattr(seals.requests, 'Session', lambda: transport)
    context = DocumentContext('unused.png')
    old = seals.build_stage_result(context, {}, [completed(url=old_url, uuid='old-request')],
                                  artifact_dir=tmp_path, artifact_url_prefix='/files/test')
    new = seals.build_stage_result(context, {}, [completed(url=new_url, uuid='new-request')],
                                  artifact_dir=tmp_path, artifact_url_prefix='/files/test')
    old_artifact = old['processing_artifacts']['seals'][0]
    new_artifact = new['processing_artifacts']['seals'][0]
    assert old_artifact['body_url'] != new_artifact['body_url']
    assert len(transport.calls) == 2
    with Image.open(tmp_path / 'seals' / Path(new_artifact['body_url']).name) as actual:
        assert tuple(np.asarray(actual)[2, 2]) == (0, 0, 255)


def test_fragments_from_different_physical_stamps_cannot_form_a_match():
    fragments = [completed(0, '目标有限公司'), completed(1, '收货专用章')]
    result = seals.build_stage_result(DocumentContext('unused.png'),
        {'签章要求': '目标有限公司收货专用章'}, fragments)
    check = result['seal_check']
    assert check['status'] != '匹配'
    assert check['recognized'] in {'目标有限公司', '收货专用章'}


def test_sender_stamp_is_inspectable_but_cannot_satisfy_customer_requirement():
    requirement = '目标有限公司收货专用章'
    sender = completed(0, requirement)
    sender['region'] = {'role': '发货单位章'}
    customer = completed(1, '另一家有限公司收货章')
    customer['region'] = {'role': '收货客户章'}
    result = seals.build_stage_result(DocumentContext('unused.png'), {'签章要求': requirement}, [sender, customer])
    assert len(result['processing_artifacts']['seals']) == 2
    assert result['seal_check']['status'] != '匹配'
    assert result['seal_check']['recognized'] == '另一家有限公司收货章'
    assert [candidate['index'] for candidate in result['seal_check']['candidates']] == [1]


@pytest.mark.parametrize('has_returned_image', [True, False])
def test_server_artifact_standard_registration_and_shape_comparison(tmp_path, monkeypatch, has_returned_image):
    from receipt_ocr.domain.seal_standards import seal_candidates, standard_comparison

    url = 'https://images.example/standard-source.png'
    transport = ImageTransport({url: png_bytes('blue', (7, 13))})
    monkeypatch.setattr(seals.requests, 'Session', lambda: transport)
    requirement = '目标有限公司收货专用章'
    item = completed(text=requirement, color='蓝色', shape='椭圆章',
                     url=url if has_returned_image else None)
    item['region'] = {'role': '收货客户章'}
    result = seals.build_stage_result(DocumentContext('unused.png'), {'签章要求': requirement}, [item],
                                      artifact_dir=tmp_path, artifact_url_prefix='/files/test')
    result['fields']['签章要求'] = requirement
    artifact = result['processing_artifacts']['seals'][0]
    candidate, = seal_candidates(result)
    assert candidate['source'] == 'danzhengtong'
    assert candidate['shape'] == 'ellipse'
    assert candidate['color'] == 'blue'
    assert candidate['recognized'] == requirement
    assert candidate['can_register'] is has_returned_image
    if has_returned_image:
        assert candidate['image_url'] == artifact['returned_image_url']
        assert candidate['image_url'] != item['input_url']
    else:
        assert candidate['image_url'] == ''
        assert candidate['image_url'] != artifact['color_isolated_url']
    standard = {'shape': 'ellipse', 'color': 'blue'}
    compared = standard_comparison(result, standard)
    assert compared['status'] == 'matched'
    assert compared['candidates'][0]['shape_status'] == '匹配'
    wrong_shape = standard_comparison(result, {**standard, 'shape': 'round'})
    assert wrong_shape['status'] == 'unmatched'
    assert wrong_shape['candidates'][0]['shape_status'] == '不匹配'


@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_unreachable_returned_image_never_claims_local_image_was_rotated(tmp_path, monkeypatch, missing):
    transport = ImageTransport(error=TimeoutError('image download timed out'))
    monkeypatch.setattr(seals.requests, 'Session', lambda: transport)
    item = completed(url=None if missing else 'https://images.example/unavailable.png')
    result = seals.build_stage_result(DocumentContext('unused.png'),
        {'签章要求': '测试有限公司收货专用章'}, [item],
        artifact_dir=tmp_path, artifact_url_prefix='/files/test')
    assert result['seal_check']['status'] == '匹配'
    artifact = result['processing_artifacts']['seals'][0]
    assert artifact['returned_image_status'] == ('missing' if missing else 'unavailable')
    assert artifact['returned_image_error']
    assert artifact['body_url'] == artifact['returned_image_url'] == artifact['color_isolated_oriented_url'] == ''
    assert artifact['input_url'] == '/files/input-0.png'
    assert not list(tmp_path.rglob('*returned*.png'))


def test_sync_plan_uses_separate_page_and_seal_models_without_local_ocr(tmp_path, monkeypatch):
    source = tmp_path / 'receipt.png'
    Image.new('RGB', (120, 80), 'white').save(source)
    monkeypatch.setattr(seals, 'detect_seal_regions', lambda *_: [SealRegion(0, 0, 1, 1, 'red', '客户章', .1)])
    monkeypatch.setattr(dzt, '_software_settings', credentials(model_id='page-scene', seal_model_id='stamp-scene'))
    monkeypatch.setattr(dzt.time, 'sleep', lambda *_: None)
    requests, uploads, accepted = [], [], {}
    real_client = dzt.Client
    returned_url = 'https://images.example/server-returned.png'
    image_transport = ImageTransport({returned_url: png_bytes()})
    monkeypatch.setattr(seals.requests, 'Session', lambda: image_transport)

    def response(body):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    def request(method, url, **kwargs):
        if 'files' in kwargs:
            uploads.append(kwargs['files']['file'][1].read())
            return response({'status': True, 'fileId': f'file-{len(uploads)}'})
        if method == 'POST':
            payload = kwargs['json']
            requests.append(payload)
            uuid = f"request-{payload['modelId']}"
            accepted[uuid] = payload['modelId']
            return response({'code': 200, 'status': True, 'data': {'reqUuid': uuid}})
        scene = accepted[kwargs['params']['reqUuid']]
        fields = commit('目标有限公司收货专用章') if scene == 'stamp-scene' else {
            name: {'value': value} for name, value in {
                '客户名称': '目标有限公司', '签章要求': '目标有限公司收货专用章',
                '要求到货': '2026-11-05', '签收日期': '2026-11-05',
                '收货客户印章': '旧整页结果不应作为客户印章',
            }.items()
        }
        return response({'code': 200, 'status': True, 'data': {
            'commitResult': fields, 'imgUrls': [returned_url] if scene == 'stamp-scene' else [],
        }})

    transport = SimpleNamespace(request=request)
    def client(settings=None, **kwargs):
        return real_client(settings=settings, transport=transport, **kwargs)
    monkeypatch.setattr(dzt, 'Client', client)
    monkeypatch.setattr(seals, 'Client', client)
    def forbidden(*args, **kwargs):
        raise AssertionError('remote-only plan invoked local OCR')
    analyzer = SimpleNamespace(seal_api=SimpleNamespace(enabled=False), run_stage=forbidden)
    with capture_recognition_calls(source) as captured:
        result = run_configured(analyzer, source, config={
            'fields': ['danzhengtong'], 'date': ['danzhengtong'], 'seal': ['danzhengtong'],
        }, artifact_dir=tmp_path / 'artifacts', artifact_url_prefix='/files/test')
    assert len(requests) == 2
    assert {item['modelId'] for item in requests} == {'page-scene', 'stamp-scene'}
    assert len(uploads) == 2
    assert result['fields']['客户名称'] == '目标有限公司'
    assert result['date_check']['actual'] == '2026-11-05'
    assert result['date_check']['status'] == '匹配'
    assert result['seal_check']['recognized'] == '目标有限公司收货专用章'
    assert result['seal_check']['provider_color'] == '蓝色'
    assert result['seal_check']['provider_seal_type'] == '椭圆章'
    assert result['processing_artifacts']['seals'][0]['returned_image_status'] == 'downloaded'
    seal_calls = [item for item in captured.snapshot()['calls'] if item['stage'] == 'seal']
    assert len(seal_calls) == 1 and seal_calls[0]['action'] == '印章专用场景识别'


def test_seal_only_plan_never_invokes_page_scene(tmp_path, monkeypatch):
    source = tmp_path / 'stamp.png'
    Image.new('RGB', (120, 80), 'white').save(source)
    monkeypatch.setattr(dzt, '_software_settings', credentials(model_id='page-scene', seal_model_id='stamp-scene'))
    monkeypatch.setattr(seals, 'detect_seal_regions', lambda *_: [SealRegion(0, 0, 1, 1, 'red', '客户章', .1)])
    calls = []
    def recognize(self, path, *, normalizer=None):
        calls.append(self.settings.model_id)
        return normalizer(commit('目标有限公司收货专用章')), {
            'reqUuid': 'seal-only', 'result': {'commitResult': commit('目标有限公司收货专用章')},
        }
    monkeypatch.setattr(dzt.Client, 'recognize', recognize)
    monkeypatch.setattr(dzt, 'stage_result', lambda *args, **kwargs: pytest.fail('page scene invoked for seal only'))
    analyzer = SimpleNamespace(seal_api=SimpleNamespace(enabled=False))
    result = run_configured(analyzer, source, config={'seal': ['danzhengtong']},
        previous_fields={'签章要求': '目标有限公司收货专用章'}, artifact_dir=tmp_path / 'artifacts')
    assert calls == ['stamp-scene']
    assert result['seal_check']['status'] == '匹配'
    assert result['recognition_status']['fields'] == '未执行'
