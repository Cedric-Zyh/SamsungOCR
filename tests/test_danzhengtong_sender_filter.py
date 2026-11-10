"""Use source pixels and footer pairing before discarding a possible customer."""
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from receipt_ocr import danzhengtong_seal as adapter
from receipt_ocr.imaging.contracts import SealRegion


def region(x=.08, y=.70, width=.30, height=.14, *, role='收货客户章', color='red'):
    return SealRegion(x, y, width, height, color, role, .12)


def draw_border(image, candidate, shape):
    painter = ImageDraw.Draw(image)
    width, height = image.size
    left, top = round(candidate.x * width), round(candidate.y * height)
    right, bottom = round((candidate.x + candidate.width) * width), round((candidate.y + candidate.height) * height)
    box = left + 5, top + 5, right - 5, bottom - 5
    ink = (225, 40, 40) if candidate.color == 'red' else (40, 40, 225)
    if shape == 'ellipse':
        painter.ellipse(box, outline=ink, width=7)
    elif shape == 'circle':
        span = min(right - left, bottom - top) - 10
        cx, cy = (left + right) / 2, (top + bottom) / 2
        painter.ellipse((round(cx - span / 2), round(cy - span / 2),
                         round(cx + span / 2), round(cy + span / 2)), outline=ink, width=7)
    elif shape == 'rectangle':
        painter.rectangle(box, outline=ink, width=7)
    elif shape == 'no_border':
        # Disconnected type-like marks supply colour but no measured rim.
        for index in range(6):
            x = left + 30 + index * 35
            painter.line((x, top + 45, x, bottom - 35), fill=ink, width=5)
            painter.line((x - 8, top + 55, x + 12, top + 55), fill=ink, width=4)


def page(tmp_path, left=None, right=None, *, shape='ellipse', with_right=True):
    left = left or region()
    right = right or region(.66, .69, .18, .18)
    image = Image.new('RGB', (1000, 1000), 'white')
    draw_border(image, left, shape)
    if with_right:
        draw_border(image, right, 'circle')
    source = tmp_path / 'source-page.png'
    image.save(source)
    return source, left, right


@pytest.mark.parametrize('color', ['red', 'blue'])
def test_left_footer_horizontal_oval_is_excluded_only_with_separate_right_customer(tmp_path, color):
    source, left, right = page(tmp_path, region(color=color))
    assert adapter.sender_region_indices(source, [left, right]) == {0}
    # A customer stamp placed on the left must remain when it is alone.
    assert adapter.sender_region_indices(source, [left]) == set()


def test_public_adapter_exports_shared_filter_and_returns_list_positions(tmp_path):
    from receipt_ocr.imaging.seal_roles import sender_region_indices
    assert adapter.sender_region_indices is sender_region_indices
    source, left, right = page(tmp_path)
    assert adapter.sender_region_indices(source, [right.to_dict(), left.to_dict()]) == {1}


@pytest.mark.parametrize('shape', ['circle', 'rectangle', 'no_border'])
def test_left_round_rectangular_or_borderless_ink_is_not_discarded(tmp_path, shape):
    source, left, right = page(tmp_path, shape=shape)
    assert adapter.sender_region_indices(source, [left, right]) == set()


def test_vertical_oval_cannot_supply_horizontal_shipping_rim_evidence(tmp_path):
    source, left, right = page(tmp_path, region(.08, .65, .14, .29))
    assert adapter.sender_region_indices(source, [left, right]) == set()


@pytest.mark.parametrize('left_role', ['', '未知章', '不确定'])
def test_unknown_left_stamp_stays_even_with_oval_pixels_and_right_customer(tmp_path, left_role):
    source, left, right = page(tmp_path, region(role=left_role))
    assert adapter.sender_region_indices(source, [left, right]) == set()


@pytest.mark.parametrize('right_role', ['', '未知章', '发货单位章'])
def test_unknown_or_shipping_right_stamp_is_not_a_customer_anchor(tmp_path, right_role):
    right = region(.66, .69, .18, .18, role=right_role)
    source, left, right = page(tmp_path, right=right)
    assert adapter.sender_region_indices(source, [left, right]) == ({1} if right_role == '发货单位章' else set())


@pytest.mark.parametrize('left,right', [
    (region(.28, .70, .30, .14), region(.66, .69, .18, .18)),  # left centre > .42
    (region(.08, .54, .30, .14), region(.66, .56, .18, .18)),  # above footer
    (region(), region(.66, .43, .18, .18)),  # different vertical band
    (region(), region(.66, .49, .18, .18)),  # centres .19 apart, boxes do not overlap
    (region(.08, .735, .16, .07), region(.66, .635, .13, .08)),  # close centres, no vertical overlap
    (region(), region(.46, .69, .16, .18)),  # right centre < .55
    (region(), region(.365, .69, .40, .18)),  # overlapping, without horizontal gap
])
def test_positions_alone_do_not_filter_without_full_pairing_constraints(tmp_path, left, right):
    source, left, right = page(tmp_path, left, right)
    assert adapter.sender_region_indices(source, [left, right]) == set()


def test_inclusive_left_footer_boundary_can_still_filter_a_clear_pair(tmp_path):
    source, left, right = page(tmp_path, region(.27, .55, .30, .14), region(.66, .60, .18, .18))
    assert left.x + left.width / 2 == pytest.approx(.42)
    assert left.y + left.height / 2 == pytest.approx(.62)
    assert adapter.sender_region_indices(source, [left, right]) == {0}


@pytest.mark.parametrize('invalid', [
    None, {}, {'role': '收货客户章'},
    {'x': float('nan'), 'y': .7, 'width': .3, 'height': .14, 'color': 'red', 'role': '收货客户章'},
    {'x': -.08, 'y': .7, 'width': .3, 'height': .14, 'color': 'red', 'role': '收货客户章'},
    {'x': .08, 'y': .7, 'width': -.3, 'height': .14, 'color': 'red', 'role': '收货客户章'},
    {'x': .08, 'y': .7, 'width': .3, 'height': 0, 'color': 'red', 'role': '收货客户章'},
    {'x': .08, 'y': .9, 'width': .3, 'height': .2, 'color': 'red', 'role': '收货客户章'},
])
def test_invalid_or_incomplete_candidates_are_preserved_without_crashing(tmp_path, invalid):
    source, _, right = page(tmp_path)
    assert adapter.sender_region_indices(source, [invalid, right.to_dict()]) == set()


def test_missing_source_still_excludes_explicit_sender_roles_without_inference(tmp_path):
    candidates = [region(role='未知章'), {'role': '发货单位章'}, region(.66, .69, .18, .18)]
    assert adapter.sender_region_indices(None, candidates) == {1}
    assert adapter.sender_region_indices(tmp_path / 'missing.png', candidates) == {1}
    assert adapter.sender_region_indices(None, []) == set()


def test_dictionary_input_uses_pixels_not_remote_shape_hints(tmp_path):
    source, left, right = page(tmp_path)
    left_dict = left.to_dict()
    left_dict.update(qingtong_cls='rectangle', provider_seal_type='圆章', shape='round')
    assert adapter.sender_region_indices(source, [left_dict, right.to_dict()]) == {0}


def test_fresh_preparation_filters_before_cropping_and_retains_original_index(tmp_path, monkeypatch):
    source, left, right = page(tmp_path)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    detections, crops = [], []
    save_original, save_enhanced = adapter.save_region_crop, adapter.save_color_isolated_seal

    def detect(path):
        detections.append(path)
        return [left, right]

    def crop(path, destination, candidate):
        crops.append(('original', candidate))
        return save_original(path, destination, candidate)

    def enhance(path, destination, candidate):
        crops.append(('enhanced', candidate))
        return save_enhanced(path, destination, candidate)

    def forbidden_client(*args, **kwargs):
        raise AssertionError('preparing crops must not send any remote request')

    monkeypatch.setattr(adapter, 'detect_seal_regions', detect)
    monkeypatch.setattr(adapter, 'save_region_crop', crop)
    monkeypatch.setattr(adapter, 'save_color_isolated_seal', enhance)
    monkeypatch.setattr(adapter, 'Client', forbidden_client)
    destination = tmp_path / 'artifacts'
    prepared = adapter.prepare_requests(source, destination, '/files/artifacts/test')
    assert detections == [source]
    assert crops == [('original', right), ('enhanced', right)]
    assert [item['index'] for item in prepared] == [1]
    assert Path(prepared[0]['input_path']) == destination / 'seals' / 'dzt-seal-1-input.png'
    assert prepared[0]['local_orientation'] == 'not_run'
    assert not list((destination / 'seals').glob('dzt-seal-0-*'))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
