"""Portable seed data: active sample images/labels and current seal standards."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from uuid import uuid4
import warnings
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from PIL import Image
from flask import jsonify, request, send_file

from receipt_ocr.domain.seal_standards import requirement_key
from receipt_ocr.persistence.database import now_iso

FORMAT = 'samsung-receipt-bootstrap'
MAX_TOTAL = 450 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
MAX_ITEMS = 10000


def selected_kinds(scope):
    if scope not in {'all', 'samples', 'standards'}:
        raise ValueError('请选择样单、标准章或全部数据')
    return {'samples', 'standards'} if scope == 'all' else {scope}


def _image(data):
    if not data or len(data) > MAX_IMAGE:
        raise ValueError('数据包中的单张图片为空或超过 20 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {'PNG', 'JPEG', 'BMP', 'WEBP'}:
                    raise ValueError('不支持的图片格式')
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('数据包包含损坏或不支持的图片') from exc


def export_package(ctx, service, *, scope='all'):
    kinds = selected_kinds(scope)
    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    manifest = {'format': FORMAT, 'version': 1, 'created_at': now_iso(), 'samples': [], 'standards': []}
    total = 0
    try:
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            def add(path, member):
                nonlocal total
                if not path.is_file() or path.stat().st_size > MAX_IMAGE:
                    raise ValueError('来源图片缺失或超过 20 MB，请检查样单和标准章')
                data = path.read_bytes()
                _image(data)
                total += len(data)
                if total > MAX_TOTAL:
                    raise ValueError('初始化数据超过 450 MB，无法导出')
                archive.writestr(member, data)

            catalog = service.library.catalog() if 'samples' in kinds else {}
            standards = ctx.database.list_seal_standards() if 'standards' in kinds else []
            if len(catalog) + len(standards) > MAX_ITEMS:
                raise ValueError('初始化数据最多包含 10000 项')
            for index, (name, entry) in enumerate(sorted(catalog.items())):
                member = f'samples/{index}{Path(name).suffix.lower()}'
                add(service.library.source(name), member)
                manifest['samples'].append({'filename': name, 'entry': entry, 'image': member})
            directory = ctx.database.path.parent / 'seal-standards'
            for index, standard in enumerate(standards):
                name = standard['image_url'].rsplit('/', 1)[-1]
                member = f'standards/{index}.png'
                add(directory / name, member)
                manifest['standards'].append({key: standard[key] for key in
                    ('requirement', 'recognized', 'shape', 'color', 'source_filename')})
                manifest['standards'][-1]['image'] = member
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        if output.tell() > MAX_TOTAL:
            raise ValueError('压缩后的初始化数据包超过 450 MB，无法导出')
        output.seek(0)
        return output
    except Exception:
        output.close()
        raise


def read_package(stream):
    """Validate everything before writing; ZIP members are never extracted as paths."""
    try:
        with ZipFile(stream) as archive:
            infos = archive.infolist()
            if (len(infos) > MAX_ITEMS + 1 or len({i.filename for i in infos}) != len(infos)
                    or sum(i.file_size for i in infos) > MAX_TOTAL + 16 * 1024 * 1024):
                raise ValueError('数据包过大或包含重复文件')
            if archive.getinfo('manifest.json').file_size > 16 * 1024 * 1024:
                raise ValueError('数据包清单过大')
            manifest = json.loads(archive.read('manifest.json'))
            if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
                    or type(manifest.get('version')) is not int or manifest.get('version') != 1):
                raise ValueError('请选择本程序导出的初始化数据包（版本 1）')
            samples, standards = manifest.get('samples'), manifest.get('standards')
            if not isinstance(samples, list) or not isinstance(standards, list) or len(samples) + len(standards) > MAX_ITEMS:
                raise ValueError('数据包清单无效')
            members, names, keys = {'manifest.json'}, set(), set()
            for kind, items in [('samples', samples), ('standards', standards)]:
                for item in items:
                    if not isinstance(item, dict):
                        raise ValueError('数据包条目无效')
                    member = item.get('image')
                    if (not isinstance(member, str) or not member.startswith(kind + '/')
                            or '\\' in member or len(member.split('/')) != 2
                            or member.split('/')[-1] in {'', '.', '..'} or member in members):
                        raise ValueError('数据包图片路径无效')
                    members.add(member)
                    if kind == 'samples':
                        name, entry = item.get('filename'), item.get('entry')
                        if (not isinstance(name, str) or not name or len(name) > 200
                                or '/' in name or '\\' in name or any(ord(c) < 32 for c in name)
                                or Path(name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}
                                or name in names or not isinstance(entry, dict)
                                or not isinstance(entry.get('fields', {}), dict)):
                            raise ValueError('数据包样单名称或标注无效')
                        names.add(name)
                    else:
                        for field in ('requirement', 'recognized', 'shape', 'color', 'source_filename'):
                            if not isinstance(item.get(field), str) or len(item[field]) > 10000:
                                raise ValueError('数据包标准章信息无效')
                        key = requirement_key(item['requirement'])
                        if (not key or key in keys or item['shape'] not in {'round', 'ellipse', 'rectangle'}
                                or item['color'] not in {'red', 'blue'}):
                            raise ValueError('数据包签章要求重复或章型无效')
                        keys.add(key)
                    if archive.getinfo(member).file_size > MAX_IMAGE:
                        raise ValueError('数据包中的单张图片超过 20 MB')
                    data = archive.read(member)
                    _image(data)
                    if kind == 'standards':
                        # All registered standards use PNG, regardless of package encoding.
                        with Image.open(BytesIO(data)) as image:
                            normalized = BytesIO()
                            image.convert('RGB').save(normalized, 'PNG')
                            data = normalized.getvalue()
                        if len(data) > MAX_IMAGE:
                            raise ValueError('标准章转换为 PNG 后超过 20 MB')
                    item['_data'] = data
            if members != {i.filename for i in infos}:
                raise ValueError('数据包包含清单以外的文件')
            return samples, standards
    except (BadZipFile, KeyError, json.JSONDecodeError, UnicodeError, RuntimeError,
            NotImplementedError, EOFError, zlib.error) as exc:
        raise ValueError('数据包损坏或格式无效，请重新导出') from exc


def import_package(ctx, service, stream, *, scope='all'):
    kinds = selected_kinds(scope)
    samples, standards = read_package(stream)
    if 'samples' not in kinds:
        samples = []
    if 'standards' not in kinds:
        standards = []
    created = []
    counts = {'samples_added': 0, 'samples_skipped': 0, 'standards_added': 0, 'standards_skipped': 0}
    directory = ctx.database.path.parent / 'seal-standards'
    directory.mkdir(exist_ok=True)
    try:
        with ctx.database.connect() as con:
            # One transaction covers both databases and preserves all local rows.
            con.execute('ATTACH DATABASE ? AS seed_samples', (str(service.database.path),))
            con.execute('BEGIN IMMEDIATE')
            reserved = set()
            if samples:
                reserved.update(service.library.catalog(include_deleted=True, connection=con))
                reserved.update(row[0] for row in con.execute('SELECT filename FROM seed_samples.sample_library'))
                reserved.update(row[0] for row in con.execute('SELECT filename FROM seed_samples.recognition_jobs'))
            for item in samples:
                if item['filename'] in reserved:
                    counts['samples_skipped'] += 1
                    continue
                name = uuid4().hex + Path(item['filename']).suffix.lower()
                target = service.library.directory / name
                created.append(target)
                target.write_bytes(item['_data'])
                con.execute('INSERT INTO seed_samples.sample_library(filename,storage_name,entry_json) VALUES(?,?,?)',
                            (item['filename'], name, json.dumps(item['entry'], ensure_ascii=False)))
                counts['samples_added'] += 1
            for item in standards:
                if ctx.database.get_seal_standard(item['requirement'], _connection=con):
                    counts['standards_skipped'] += 1
                    continue
                name = uuid4().hex + '.png'
                target = directory / name
                created.append(target)
                target.write_bytes(item['_data'])
                ctx.database.save_seal_standard({
                    **{key: item[key] for key in ('requirement', 'recognized', 'shape', 'color', 'source_filename')},
                    'image_name': name, 'source_result_id': 0, 'candidate_id': '', 'source_kind': 'bootstrap',
                }, expected_revision=0, _connection=con)
                counts['standards_added'] += 1
        return counts
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise


def register_bootstrap_routes(bp, ctx, center):
    @bp.get('/api/bootstrap/export')
    def export_data():
        scope = request.args.get('scope', 'all')
        try:
            stream = export_package(ctx, center(), scope=scope)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        suffix = {'all': 'bootstrap', 'samples': 'samples', 'standards': 'standards'}[scope]
        response = send_file(stream, mimetype='application/zip', as_attachment=True,
                             download_name=f'samsung-receipt-{suffix}.zip', max_age=0)
        response.call_on_close(stream.close)
        return response

    @bp.post('/api/bootstrap/import')
    def import_data():
        upload = request.files.get('file')
        if upload is None:
            return jsonify(error='请选择初始化数据包 ZIP 文件'), 400
        try:
            return jsonify(import_package(ctx, center(), upload.stream, scope=request.form.get('scope', 'all')))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
