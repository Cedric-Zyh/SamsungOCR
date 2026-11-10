"""Independent seal scene: enhance source-direction crops, let the service orient.

No local text reader, stamp orientation, ring unfolding or glyph completion is
used on this route. Each physical stamp keeps its own request and evidence.
"""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit

from PIL import Image
import requests

from .danzhengtong import Client, Settings
from .danzhengtong_errors import safe_error_text
from .imaging.crops import save_region_crop, save_color_isolated_seal
from .imaging.detection import detect_seal_regions
from .imaging.seal_roles import sender_region_indices

SEAL_FIELDS = ('收货客户印章', '印章类型', '印章颜色')
MAX_RETURNED_IMAGE_BYTES = 20 * 1024 * 1024
SHAPE_CODES = {label: shape for shape, labels in {
    'round': ('圆形', '圆章', '圆形章', '圆形印章', 'round', 'circle'),
    'ellipse': ('椭圆', '椭圆形', '椭圆章', '椭圆形章', '椭圆形印章', 'ellipse', 'oval'),
    'rectangle': ('矩形', '矩形章', '长方形', '长方形章', '方形', '方章', '正方形章', 'rectangle', 'rect'),
}.items() for label in labels}
COLOR_CODES = {'红色': 'red', '红': 'red', 'red': 'red', '蓝色': 'blue', '蓝': 'blue', 'blue': 'blue'}


def seal_settings(settings=None):
    settings = settings or Settings.load()
    if not settings.seal_model_id:
        raise ValueError('单证通印章场景 ID 未配置')
    return replace(settings, model_id=settings.seal_model_id)


def normalize_seal_result(commit):
    fields, metadata = {}, {}
    for name in SEAL_FIELDS:
        item = commit.get(name)
        if item is None:
            item = {'value': ''}
        if not isinstance(item, dict):
            raise ValueError(f'单证通印章字段格式错误：{name}')
        value = item.get('value')
        if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
            raise ValueError(f'单证通印章字段值格式错误：{name}')
        fields[name] = '' if value is None else str(value)
        metadata[name] = {'value': fields[name], 'original': fields[name], 'source': '单证通',
                          'provider_ratio': item.get('ratio'), 'position': item.get('position') or [],
                          'simulated': False, 'low_confidence': False}
    return {'fields': fields, 'metadata': metadata, 'simulated': False}


def prepare_requests(source, artifact_dir, artifact_url_prefix=''):
    """Persist complete, colour-enhanced stamp crops in their source direction."""
    directory = Path(artifact_dir).resolve() / 'seals'
    directory.mkdir(parents=True, exist_ok=True)
    prefix = str(artifact_url_prefix).rstrip('/')
    prepared = []
    regions = detect_seal_regions(source)
    senders = sender_region_indices(source, regions)
    for index, region in enumerate(regions):
        if index in senders:
            continue
        original = directory / f'dzt-seal-{index}-original.png'
        enhanced = directory / f'dzt-seal-{index}-input.png'
        save_region_crop(source, original, region)
        save_color_isolated_seal(source, enhanced, region)
        prepared.append({'index': index, 'input_path': str(enhanced),
                         'input_sha256': hashlib.sha256(enhanced.read_bytes()).hexdigest(),
                         'input_url': f'{prefix}/seals/{enhanced.name}' if prefix else '',
                         'original_url': f'{prefix}/seals/{original.name}' if prefix else '',
                         'color': region.color, 'region': region.to_dict(),
                         'local_orientation': 'not_run'})
    return prepared


def _public_image_source(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


def download_returned_image(url, destination, *, transport=None):
    """Cache server pixels without sending OCR credentials or rotating them."""
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('单证通返回的旋正图片地址无效')
    destination = Path(destination)
    if destination.is_file():
        with Image.open(destination) as cached:
            cached.verify()
        return destination
    session = transport or requests.Session()
    if transport is None:
        session.trust_env = False
    response = None
    try:
        response = session.get(url, stream=True, timeout=(5, 15))
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_RETURNED_IMAGE_BYTES:
                raise ValueError('单证通返回的旋正图片超过 20 MB')
            chunks.append(chunk)
        with Image.open(BytesIO(b''.join(chunks))) as returned:
            returned.load()
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix('.tmp')
            returned.convert('RGB').save(temporary, format='PNG')
            temporary.replace(destination)
        return destination
    finally:
        if response is not None:
            response.close()
        if transport is None:
            session.close()


def returned_image_target(item, artifact_dir, prefix=''):
    """Derive an immutable cache target without exposing a signed source URL."""
    urls = (item.get('result') or {}).get('imgUrls')
    urls = [url for url in urls if isinstance(url, str) and url] if isinstance(urls, list) else []
    if not urls or artifact_dir is None:
        return None
    identity = hashlib.sha256((str(item.get('req_uuid', '')) + ':' + urls[0]).encode()).hexdigest()[:16]
    destination = Path(artifact_dir) / 'seals' / f'dzt-seal-{item.get("index", 0)}-{identity}-returned.png'
    url = f'{str(prefix).rstrip("/")}/seals/{destination.name}' if prefix else ''
    return urls[0], destination, url


def _returned_artifact(item, data, artifact_dir, prefix, *, download_mode='inline'):
    index = item.get('index', 0)
    fields = normalize_seal_result(data.get('commitResult') or {})['fields']
    artifact = {'schema_version': 2, 'index': index, 'region_id': f'dzt-seal-{index}',
                'provider': 'danzhengtong', 'source': '单证通印章专用场景', 'ocr_backend': '单证通',
                'color': COLOR_CODES.get(fields['印章颜色'].strip().lower(), ''),
                'input_color': item.get('color', ''), 'role': (item.get('region') or {}).get('role', ''),
                'shape': fields['印章类型'], 'shape_code': SHAPE_CODES.get(fields['印章类型'].strip().lower(), ''),
                'shape_source': '单证通印章专用场景', 'provider_color': fields['印章颜色'],
                'provider_seal_type': fields['印章类型'], 'provider_text': fields['收货客户印章'],
                'combined_text': fields['收货客户印章'], 'original_url': item.get('original_url', ''),
                'input_url': item.get('input_url', ''), 'color_isolated_url': item.get('input_url', ''),
                'body_url': '', 'color_isolated_oriented_url': '', 'returned_image_url': '',
                'returned_image_status': 'missing', 'returned_image_error': '',
                'orientation': {'mode': 'server', 'local_rotation': False},
                'inputs': [], 'reads': [], 'errors': [], 'req_uuid': item.get('req_uuid', '')}
    enabled = Settings.load().download_seal_images
    urls = data.get('imgUrls')
    urls = [url for url in urls if isinstance(url, str) and url] if isinstance(urls, list) else []
    if not urls:
        if not enabled:
            artifact['returned_image_status'] = 'disabled'
            return artifact
        artifact['returned_image_error'] = '单证通未返回旋正图片地址'
        return artifact
    if artifact_dir is None:
        if not enabled:
            artifact['returned_image_status'] = 'disabled'
            return artifact
        artifact.update(returned_image_status='unavailable', returned_image_error='未配置旋正图片缓存目录')
        return artifact
    try:
        artifact['returned_image_source_url'] = _public_image_source(urls[0])
        source, destination, url = returned_image_target({**item, 'result': data}, artifact_dir, prefix)
        cached = False
        if destination.is_file():
            try:
                with Image.open(destination) as image:
                    image.verify()
                cached = True
            except (OSError, ValueError):
                pass
        if not cached:
            if not enabled:
                artifact['returned_image_status'] = 'disabled'
                return artifact
            if download_mode == 'deferred':
                artifact['returned_image_status'] = 'pending'
                return artifact
            download_returned_image(source, destination)
        artifact.update(returned_image_status='downloaded', returned_image_url=url,
                        body_url=url, color_isolated_oriented_url=url)
    except Exception as exc:
        artifact.update(returned_image_status='unavailable',
                        returned_image_error=f'单证通旋正图片下载失败（{type(exc).__name__}）')
    return artifact


def build_stage_result(context, previous_fields, results, trace=None, *, artifact_dir=None, artifact_url_prefix='',
                       download_mode='inline'):
    """Project only new-scene evidence; never combine text from different stamps."""
    from .application.pipeline import empty_result
    from .domain.parsing.parsing_seals import compare_seal_text_strict
    result = empty_result(context)
    artifacts, eligible_artifacts, texts, metadata, failures = [], [], [], [], []
    for item in results:
        if item.get('status') not in {'completed', 'received'}:
            if item.get('error'):
                failures.append(str(item['error']))
            continue
        data = item.get('result') or {}
        normalized = normalize_seal_result(data.get('commitResult') or {})
        text = normalized['fields']['收货客户印章']
        artifact = _returned_artifact(item, data, artifact_dir, artifact_url_prefix, download_mode=download_mode)
        artifacts.append(artifact)
        if artifact.get('role') != '发货单位章':
            texts.append(text)
            metadata.append(normalized)
            eligible_artifacts.append(artifact)
    check = compare_seal_text_strict((previous_fields or {}).get('签章要求', ''), texts)
    selected = next((index for index, text in enumerate(texts) if text == check.get('recognized')), 0)
    selected_fields = metadata[selected]['fields'] if metadata else {}
    check.update(backend='单证通', source='单证通', recognition_mode='danzhengtong', simulated=False,
                 model_id=(trace or {}).get('model_id', ''), provider_color=selected_fields.get('印章颜色', ''),
                 provider_seal_type=selected_fields.get('印章类型', ''),
                 provider_metadata=deepcopy(metadata[selected]['metadata']) if metadata else {},
                 candidates=[{'index': artifact['index'], 'text': artifact['provider_text'],
                              'color': artifact['provider_color'], 'type': artifact['provider_seal_type']}
                             for artifact in eligible_artifacts])
    if failures and not metadata:
        check.update(status='识别失败', reliable=False, message='；'.join(failures))
    elif not results:
        check.update(message='未检测到可提交单证通的完整印章')
    result.update(seal_check=check, danzhengtong_seal=deepcopy(trace or {}),
                  seal_regions=[deepcopy(item['region']) for item in results if item.get('region')],
                  stage_review_reasons=failures)
    result['processing_artifacts']['seals'] = artifacts
    return result


def fetch_requests(source, *, artifact_dir, artifact_url_prefix=''):
    """Fetch independent seal evidence without reading or changing page fields.

    The caller owns the artifact directory's lifetime. Comparison and returned
    image projection remain on the coordinating thread after fields are ready.
    """
    settings = seal_settings()
    client = Client(settings, action='印章专用场景识别')
    prepared = prepare_requests(source, artifact_dir, artifact_url_prefix)
    traces = []
    for item in prepared:
        try:
            _, trace = client.recognize(item['input_path'], normalizer=normalize_seal_result)
            item.update(status='completed', req_uuid=trace['reqUuid'], result=trace['result'])
            traces.append({**trace, 'index': item['index'], 'region': deepcopy(item.get('region') or {})})
        except Exception as exc:
            item.update(status='failed', error=safe_error_text(exc, settings))
    return {'requests': prepared, 'trace': {'mode': 'real', 'simulated': False,
            'model_id': settings.model_id, 'status': 'completed', 'requests': traces}}


def recognize_stage(context, previous_fields, *, artifact_dir=None, artifact_url_prefix='', cache=None):
    cache = cache if cache is not None else {}
    directory = nullcontext(str(artifact_dir)) if artifact_dir else TemporaryDirectory(prefix='dzt-seals-')
    with directory as working:
        if not cache:
            cache.update(fetch_requests(context.source, artifact_dir=working,
                                        artifact_url_prefix=artifact_url_prefix))
        return build_stage_result(context, previous_fields, cache['requests'], cache['trace'],
                                  artifact_dir=working, artifact_url_prefix=artifact_url_prefix)
