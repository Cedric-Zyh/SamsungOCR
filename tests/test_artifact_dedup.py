from pathlib import Path

from receipt_ocr.application.document_service import DocumentRecognitionService
from receipt_ocr.storage.artifact_dedup import deduplicate_artifacts


def test_completed_evidence_reuses_one_file_and_updates_nested_references(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir()
    for name, data in [('original.png', b'original'), ('color.png', b'pixels'),
                       ('ring-input.png', b'pixels'), ('oriented.png', b'pixels'),
                       ('different.png', b'changed pixels')]:
        (directory / name).write_bytes(data)
    prefix = '/files/artifacts/run'
    color = prefix + '/color.png'
    ring = prefix + '/ring-input.png'
    result = {'processing_artifacts': {'seals': [{
        'original_url': prefix + '/original.png', 'color_isolated_url': color,
        'color_isolated_oriented_url': prefix + '/oriented.png',
        'ring_input_url': ring, 'inputs': [{'image_url': ring + '?thumbnail=1'}],
    }]}, 'variants': [{'input_path': str(directory / 'ring-input.png')}]}
    saved = deduplicate_artifacts(result, directory, prefix)
    artifact = saved['processing_artifacts']['seals'][0]
    assert artifact['ring_input_url'] == color
    assert artifact['color_isolated_oriented_url'] == color
    assert artifact['inputs'][0]['image_url'] == color + '?thumbnail=1'
    assert saved['variants'][0]['input_path'] == str(directory / 'color.png')
    assert (directory / 'color.png').read_bytes() == b'pixels'
    assert (directory / 'original.png').exists() and (directory / 'different.png').exists()
    assert not (directory / 'ring-input.png').exists()
    assert not (directory / 'oriented.png').exists()


def test_originals_and_distinct_stamp_identities_stay_separate(tmp_path):
    prefix = '/files/artifacts/run'
    seals = []
    for index in range(2):
        for kind in ['original', 'color']:
            (tmp_path / f'seal-{index}-{kind}.png').write_bytes(b'identical')
        seals.append({'original_url': f'{prefix}/seal-{index}-original.png',
                      'color_isolated_url': f'{prefix}/seal-{index}-color.png'})
    result = {'seals': seals}
    assert deduplicate_artifacts(result, tmp_path, prefix) == result
    assert len(list(tmp_path.iterdir())) == 4


def test_other_runs_and_external_symlinks_are_untouched(tmp_path):
    other = tmp_path / 'history'
    other.mkdir()
    (other / 'color.png').write_bytes(b'identical')
    run = tmp_path / 'new'
    run.mkdir()
    (run / 'color.png').write_bytes(b'identical')
    (run / 'outside.png').symlink_to(other / 'color.png')
    deduplicate_artifacts({}, run, '/files/artifacts/new')
    assert (run / 'color.png').exists()
    assert (run / 'outside.png').is_symlink()
    assert (other / 'color.png').read_bytes() == b'identical'


def test_successful_recognition_compacts_before_result_is_saved(tmp_path):
    source = tmp_path / 'source.jpg'
    source.write_bytes(b'source')
    seen = {}
    def analyze(source, preview, **options):
        directory = options['artifact_dir']
        directory.mkdir(parents=True)
        for name in ['color.png', 'ring.png']:
            (directory / name).write_bytes(b'same exact evidence')
        seen['directory'] = directory
        return {'processing_artifacts': {'color_isolated_url': options['artifact_url_prefix'] + '/color.png',
                                        'ring_url': options['artifact_url_prefix'] + '/ring.png'}}
    service = DocumentRecognitionService(analyze, tmp_path / 'previews', tmp_path / 'artifacts', lambda:'now')
    result, _ = service.recognize(source, filename='receipt.jpg')
    artifact = result['processing_artifacts']
    assert artifact['color_isolated_url'] == artifact['ring_url']
    assert len(list(seen['directory'].iterdir())) == 1
    assert result['filename'] == 'receipt.jpg'
