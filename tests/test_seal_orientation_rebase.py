"""Transfer observed polygons across coarse frames; recheck final pixels."""
from PIL import Image
import numpy as np
import pytest

from receipt_ocr.recognition.seal.preprocess import combined_orientation as orient
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import _rotation_geometry, _transform_points


@pytest.mark.parametrize('old,fine,new,residual', [(45, 88.37, 135, -1.63), (180, -50.77, 90, 39.23)])
@pytest.mark.parametrize('final_text,accepted', [('售后服务专用章', True), ('', False)])
def test_rebase_maps_original_polygon_and_requires_fresh_read(
        tmp_path, monkeypatch, old, fine, new, residual, final_text, accepted):
    source = tmp_path / 'stamp.png'
    Image.new('RGB', (300, 200), 'white').save(source)
    polygon = [[20, 30], [180, 30], [180, 60], [20, 60]]
    original = source.read_bytes()
    old_matrix, _, _ = _rotation_geometry(300, 200, old)
    new_matrix, _, _ = _rotation_geometry(300, 200, new)
    item = dict(angle=old, fine_rotation=fine, band_text='售后服务专用章',
                band_confidence=.99, boxes=[dict(text='售后服务专用章', confidence=.99,
                angle=fine, points=_transform_points(polygon, old_matrix))])
    def reread(s, scratch, bands, angle, *, model_variant, observed_boxes):
        assert angle == new
        assert observed_boxes[0]['angle'] == pytest.approx(residual)
        np.testing.assert_allclose(observed_boxes[0]['points'],
                                   _transform_points(polygon, new_matrix), atol=.03)
        # Fresh OCR can reject even though the earlier reading was perfect.
        return dict(angle=angle, fine_rotation=residual, band_text=final_text,
                    band_confidence=.99, error='')
    monkeypatch.setattr(orient, '_four_way_candidate', reread)
    trial = orient._rebase_large_angle(source, tmp_path, tmp_path, item, model_variant='v6')
    fallback = dict(angle=0, fine_rotation=0, band_text='', band_confidence=0, error='')
    selected = orient._select_four_way_candidate([fallback, trial])
    assert (selected is trial) is accepted
    assert trial['post_rotation_verified'] is accepted
    assert source.read_bytes() == original


@pytest.mark.parametrize('fine,text,score', [
    (44, '售后服务专用章', .99), (95, '售后服务专用章', .99),
    (float('nan'), '售后服务专用章', .99),
    (88, '公司', .99), (88, '售后服务专用章', .1),
    (88, '售后服务专用章', float('nan')),
])
def test_rebase_does_not_retry_unverified_or_small_angles(tmp_path, monkeypatch, fine, text, score):
    monkeypatch.setattr(orient, '_four_way_candidate', lambda *a, **k: pytest.fail('must not retry'))
    item = dict(angle=45, fine_rotation=fine, band_text=text,
                band_confidence=score, boxes=[{}])
    assert orient._rebase_large_angle('unused.png', tmp_path, tmp_path, item, model_variant='v6') is None
