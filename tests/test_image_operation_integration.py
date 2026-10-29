"""Image timing covers real derivatives without invoking OCR or making new files."""
import cv2
import numpy as np

from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.imaging.crops import save_color_isolated_seal, save_region_crop
from receipt_ocr.imaging.date import save_receipt_date_crop
from receipt_ocr.imaging.unwrap import save_unwrapped_seal
from receipt_ocr.imaging.working_images import image_workspace_scope, cached_transform
from receipt_ocr.recognition.date.preprocess.frame import _save_positioned_outer_frame_clean
from receipt_ocr.recognition.seal.preprocess.orientation_geometry import rotate_stamp_image
from receipt_ocr.runtime.calls import capture_recognition_calls
from receipt_ocr.runtime.progress import model_stage


def test_real_seal_derivatives_record_image_work_separately_and_reuse_rotation(tmp_path):
    source = tmp_path / 'receipt.png'
    pixels = np.full((240, 320, 3), 255, dtype=np.uint8)
    cv2.circle(pixels, (160, 120), 75, (0, 0, 220), 4)
    cv2.putText(pixels, 'ABCD', (105, 120), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 220), 2)
    cv2.imwrite(str(source), pixels)
    region = SealRegion(x=.2, y=.1, width=.6, height=.8, color='red', role='customer', pixel_ratio=.1)
    original, color, rotated, repeat, ring = [tmp_path / name for name in
        ('original.jpg', 'color.png', 'rotated.png', 'repeat.png', 'ring.png')]
    with capture_recognition_calls(source) as trace, image_workspace_scope():
        with model_stage('seal', 'paddle_v6'):
            save_region_crop(source, original, region)
            save_color_isolated_seal(source, color, region)
            rotate_stamp_image(color, rotated, 30)
            rotate_stamp_image(color, repeat, 30)
            save_unwrapped_seal(rotated, ring, None)
    snapshot = trace.snapshot()
    assert snapshot['summary']['total'] == 0
    steps = {step['operation']: step for step in snapshot['image_processing']['steps']}
    assert {'crop', 'color_cleanup', 'resize', 'rotation', 'unwrap'} <= steps.keys()
    assert all(step['stage'] == 'seal' for step in steps.values())
    assert steps['rotation']['calls'] == 1  # Reusing identical pixels is not another rotation.
    assert all(step['failed'] == 0 and step['seconds'] >= 0 for step in steps.values())
    assert snapshot['image_processing']['seconds'] > 0
    np.testing.assert_array_equal(cv2.imread(str(rotated)), cv2.imread(str(repeat)))
    assert {p.name for p in tmp_path.iterdir()} == {
        'receipt.png', 'original.jpg', 'color.png', 'rotated.png', 'repeat.png', 'ring.png'}


def test_date_cleanup_and_cached_resize_are_measured_without_changing_pixels(tmp_path):
    source, derivative, cleaned = [tmp_path / name for name in ('source.png', 'date.png', 'cleaned.png')]
    pixels = np.full((800, 1200, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (900, 450), (1190, 490), (0, 0, 0), 2)
    cv2.imwrite(str(source), pixels)
    with capture_recognition_calls(source) as trace, image_workspace_scope():
        with model_stage('date', 'paddle_v6'):
            save_receipt_date_crop(source, derivative, .5)
            _save_positioned_outer_frame_clean(derivative, cleaned)
            calls = []
            def resize():
                calls.append(True)
                return cv2.resize(pixels, (200, 100))
            first = cached_transform(pixels, 'resize', ('test', 200, 100), resize)
            second = cached_transform(pixels, 'resize', ('test', 200, 100), resize)
    assert len(calls) == 1
    np.testing.assert_array_equal(first, second)
    steps = {step['operation']: step for step in trace.snapshot()['image_processing']['steps']}
    assert {'crop', 'denoise', 'resize'} <= steps.keys()
    assert steps['resize']['calls'] == 1
    assert all(step['stage'] == 'date' for step in steps.values())
    assert trace.snapshot()['summary']['total'] == 0
