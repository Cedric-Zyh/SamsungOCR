import numpy as np

from receipt_ocr.image_processing import (
    SealRegion,
    _robust_round_seal_bounds,
    _save_ellipse_annulus_unwrapped,
    _color_masks,
    _dedupe_overlapping_regions,
    _merge_split_stamp_fragments,
    _overlap_ratio,
    classify_seal_role,
    detect_seal_regions,
    save_color_isolated_seal,
    save_ellipse_normalized_seal,
    save_rectangular_seal_code_line,
    save_rectangular_seal_bands,
    save_round_seal_type_band,
    save_unwrapped_seal_bands,
    seal_region_is_rectangular,
    seal_region_is_elliptical,
    seal_region_shape,
)


def test_ellipse_annulus_unwrap_keeps_ring_ink_and_excludes_center(tmp_path):
    import cv2

    mask = np.zeros((400, 400), dtype=np.uint8)
    cv2.ellipse(mask, (200, 200), (180, 160), 0, 0, 360, 255, 4)
    cv2.ellipse(mask, (200, 200), (125, 105), 0, 0, 360, 255, 4)
    # A center marker must not leak into the ring strips. The grey marker
    # between the two borders stands in for anti-aliased stamp lettering.
    gray = np.full_like(mask, 255)
    cv2.rectangle(gray, (170, 170), (230, 230), 0, -1)
    cv2.rectangle(gray, (190, 61), (210, 76), 100, -1)
    destination = tmp_path / "ellipse-unwrapped.png"

    assert _save_ellipse_annulus_unwrapped(gray, mask, destination)
    bands = save_unwrapped_seal_bands(destination, tmp_path / "band")

    assert len(bands) == 3
    for band in bands:
        output = cv2.imread(str(band), cv2.IMREAD_GRAYSCALE)
        assert (output < 180).sum() > 100
        assert output.min() > 30  # No black center marker.


def test_ellipse_annulus_requires_two_reliable_borders(tmp_path):
    mask = np.zeros((200, 200), dtype=np.uint8)
    destination = tmp_path / "missing.png"
    assert not _save_ellipse_annulus_unwrapped(255 - mask, mask, destination)
    assert not destination.exists()


def test_ellipse_normalized_stage_stretches_color_safe_crop_to_square(tmp_path):
    import cv2

    image = np.full((220, 420, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (210, 110), (150, 55), 0, 0, 360, (60, 60, 220), 6)
    cv2.putText(image, "收货章", (145, 125), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 60, 220), 3)
    source = tmp_path / "ellipse-source.png"
    destination = tmp_path / "ellipse-normalized.png"
    assert cv2.imwrite(str(source), image)

    save_ellipse_normalized_seal(source, destination)
    output = cv2.imread(str(destination))

    assert output is not None
    assert output.shape[0] == output.shape[1]
    assert output.shape[0] > 100
    # The ink is retained after stretching, while the white background stays
    # white and does not become a full-page OCR signal.
    assert int((output[:, :, 2] > output[:, :, 1] + 20).sum()) > 100


def test_robust_round_bounds_remove_sparse_far_color_noise():
    import cv2

    mask = np.zeros((500, 900), dtype=np.uint8)
    cv2.circle(mask, (450, 250), 200, 255, 5)
    mask[248:251, 5:8] = 255
    mask[248:251, 892:895] = 255

    bounds = _robust_round_seal_bounds(mask)

    assert bounds is not None
    x1, y1, x2, y2 = bounds
    assert 0.8 <= (x2 - x1) / (y2 - y1) <= 1.25
    assert x1 > 100 and x2 < 800


def test_robust_round_bounds_leave_normal_round_mask_unchanged():
    import cv2

    mask = np.zeros((500, 500), dtype=np.uint8)
    cv2.circle(mask, (250, 250), 200, 255, 5)

    assert _robust_round_seal_bounds(mask) is None


def test_customer_stamp_in_receiving_column():
    assert classify_seal_role(0.72, 0.54) == "收货客户章"


def test_low_displaced_customer_stamp_is_not_misclassified_as_shipping_stamp():
    assert classify_seal_role(0.24, 0.70) == "收货客户章"


def test_upper_left_shipping_stamp_remains_shipping_role():
    assert classify_seal_role(0.23, 0.54) == "发货单位章"


def test_pale_colored_ink_is_kept_but_neutral_form_text_is_not():
    image = np.array([[[220, 220, 225], [220, 220, 220]]], dtype=np.uint8)
    red, _ = _color_masks(image)
    assert red[0, 0] == 255
    assert red[0, 1] == 0


def test_wide_oval_is_not_misclassified_as_rectangular_stamp(tmp_path):
    import cv2

    image = np.full((600, 900, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (450, 300), (300, 135), 0, 0, 360, (60, 60, 220), 18)
    source = tmp_path / "wide-oval.jpg"
    assert cv2.imwrite(str(source), image)
    region = SealRegion(.14, .24, .72, .52, "red", "收货客户章", .1)

    assert seal_region_is_rectangular(source, region) is False
    assert seal_region_is_elliptical(source, region) is True
    assert seal_region_shape(source, region) == "ellipse"


def test_local_oval_geometry_overrides_wrong_qingtong_rectangle_label(tmp_path):
    import cv2

    image = np.full((600, 900, 3), 255, dtype=np.uint8)
    cv2.ellipse(image, (450, 300), (300, 135), 0, 0, 360, (60, 60, 220), 18)
    source = tmp_path / "oval-wrong-remote-shape.jpg"
    assert cv2.imwrite(str(source), image)
    region = SealRegion(.14, .24, .72, .52, "red", "收货客户章", .1, "rectangle")

    assert seal_region_shape(source, region) == "ellipse"
    assert seal_region_is_rectangular(source, region) is False


def test_wide_rectangular_border_remains_rectangular_stamp(tmp_path):
    import cv2

    image = np.full((600, 900, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (130, 165), (770, 435), (60, 60, 220), 18)
    source = tmp_path / "wide-rectangle.jpg"
    assert cv2.imwrite(str(source), image)
    region = SealRegion(.12, .23, .76, .54, "red", "收货客户章", .1)

    assert seal_region_is_rectangular(source, region) is True


def test_unwrapped_seal_bands_preserve_three_exact_strips(tmp_path):
    import cv2

    source = tmp_path / "unwrapped.png"
    image = np.full((792, 2160, 3), 255, dtype=np.uint8)
    for index, value in enumerate((40, 100, 160)):
        top = index * 270
        image[top : top + 252] = value
    assert cv2.imwrite(str(source), image)

    paths = save_unwrapped_seal_bands(source, tmp_path / "band")

    assert [path.name for path in paths] == ["band-1.png", "band-2.png", "band-3.png"]
    assert [cv2.imread(str(path)).shape[:2] for path in paths] == [
        (252, 2160), (252, 2160), (252, 2160)
    ]
    assert [round(float(cv2.imread(str(path)).mean())) for path in paths] == [
        40, 100, 160
    ]


def test_rectangular_seal_bands_isolate_three_rows(tmp_path):
    import cv2

    source = tmp_path / "rectangle.png"
    image = np.full((300, 600, 3), 255, dtype=np.uint8)
    image[95:205] = 80
    assert cv2.imwrite(str(source), image)

    paths = save_rectangular_seal_bands(source, tmp_path / "rect-band")
    heights = [cv2.imread(str(path)).shape[0] for path in paths]

    assert [path.name for path in paths] == [
        "rect-band-1.png", "rect-band-2.png", "rect-band-3.png"
    ]
    assert heights == [98, 96, 98]
    assert all(cv2.imread(str(path)) is not None for path in paths)


def test_round_seal_type_band_keeps_only_lower_inner_row(tmp_path):
    import cv2

    source = tmp_path / "round-color-only.png"
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    image[560:840, 80:920] = 90
    assert cv2.imwrite(str(source), image)

    destination = save_round_seal_type_band(
        source, tmp_path / "round-type-band.png"
    )
    output = cv2.imread(str(destination))

    assert destination.name == "round-type-band.png"
    assert output is not None
    assert output.shape[:2] == (280, 840)
    assert round(float(output.mean())) == 90


def test_round_seal_type_band_uses_center_row_for_oriented_stamp(tmp_path):
    import cv2

    source = tmp_path / "round-oriented-color-only.png"
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    image[420:620, 80:920] = 90
    assert cv2.imwrite(str(source), image)

    destination = save_round_seal_type_band(
        source, tmp_path / "round-oriented-type-band.png", orientation_aligned=True
    )
    output = cv2.imread(str(destination))

    assert output is not None
    assert output.shape[:2] == (200, 840)
    assert round(float(output.mean())) == 90


def test_round_seal_type_band_can_use_detected_oriented_row_box(tmp_path):
    import cv2

    source = tmp_path / "round-focused-color-only.png"
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    image[430:570, 220:780] = 90
    assert cv2.imwrite(str(source), image)

    destination = save_round_seal_type_band(
        source,
        tmp_path / "round-focused-type-band.png",
        orientation_aligned=True,
        focus_box=[200, 400, 800, 600],
    )
    output = cv2.imread(str(destination))

    assert output is not None
    assert output.shape[:2] == (200, 600)
    assert round(float(output.mean())) == 90


def test_color_isolated_seal_keeps_red_ink_and_removes_black_form_text(tmp_path):
    import cv2

    image = np.full((200, 300, 3), 255, dtype=np.uint8)
    cv2.line(image, (20, 100), (280, 100), (0, 0, 0), 5)
    cv2.putText(image, "RED", (85, 115), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (40, 40, 220), 5)
    source = tmp_path / "source.jpg"
    destination = tmp_path / "color-isolated.png"
    assert cv2.imwrite(str(source), image)

    save_color_isolated_seal(
        source,
        destination,
        SealRegion(0, 0, 1, 1, "red", "收货客户章", .1),
    )

    output = cv2.imread(str(destination))
    assert output is not None
    center_row = output[output.shape[0] // 2]
    assert np.any(center_row[:, 2] > center_row[:, 1] + 20)
    assert np.percentile(output[:, :60].reshape(-1, 3), 10) > 245


def test_color_isolated_seal_rejects_low_saturation_table_line_fringes(tmp_path):
    import cv2

    image = np.full((200, 300, 3), 255, dtype=np.uint8)
    # JPEG/scan fringes around a black rule can have a small red-channel bias.
    image[100:103, 20:280] = (80, 90, 100)
    cv2.putText(image, "收货专用章", (45, 150), cv2.FONT_HERSHEY_SIMPLEX, .8,
                (40, 40, 220), 3)
    source = tmp_path / "fringed-source.jpg"
    destination = tmp_path / "fringed-color-isolated.png"
    assert cv2.imwrite(str(source), image)

    save_color_isolated_seal(
        source,
        destination,
        SealRegion(0, 0, 1, 1, "red", "收货客户章", .1),
    )

    output = cv2.imread(str(destination))
    assert output is not None
    # The horizontal neutral fringe is gone, while red ink remains.
    assert np.percentile(output[100:103, :, :].reshape(-1, 3), 10) > 245
    assert np.any(output[:, :, 2] > output[:, :, 1] + 20)


def test_rectangular_code_line_keeps_lower_digits_and_removes_borders(tmp_path):
    import cv2
    import numpy as np

    source = tmp_path / "code-stamp.jpg"
    destination = tmp_path / "code-line.png"
    image = np.full((420, 760, 3), 255, dtype=np.uint8)
    red = (35, 35, 220)
    cv2.rectangle(image, (60, 55), (700, 365), red, 16)
    cv2.putText(
        image, "2310637", (95, 325), cv2.FONT_HERSHEY_SIMPLEX, 2.8, red, 14
    )
    assert cv2.imwrite(str(source), image)
    region = SealRegion(0.0, 0.0, 1.0, 1.0, "red", "recipient", 0.1)
    save_rectangular_seal_code_line(source, destination, region)
    output = cv2.imread(str(destination), cv2.IMREAD_GRAYSCALE)
    assert output is not None
    assert output.shape[1] >= 1400
    assert output.shape[0] < output.shape[1]
    ink_ratio = float(np.count_nonzero(output < 128)) / output.size
    assert 0.01 < ink_ratio < 0.30


def test_cross_color_overlap_can_identify_stronger_real_ink_region():
    weak_red = SealRegion(.70, .52, .22, .15, "red", "收货客户章", .012)
    strong_blue = SealRegion(.71, .52, .21, .14, "blue", "收货客户章", .093)
    assert _overlap_ratio(weak_red, strong_blue) > .6
    assert _dedupe_overlapping_regions([weak_red, strong_blue]) == [strong_blue]


def test_vertically_split_round_stamp_fragments_are_merged_with_edge_padding():
    upper = SealRegion(.781, .430, .158, .109, "red", "收货客户章", .072)
    lower = SealRegion(.761, .498, .158, .109, "red", "收货客户章", .061)

    regions = _merge_split_stamp_fragments([upper, lower])

    assert len(regions) == 1
    assert regions[0].x < .73
    assert regions[0].x + regions[0].width > .97
    assert regions[0].height > .18


def test_nearby_duplicate_round_stamps_are_not_merged_without_vertical_overlap():
    upper = SealRegion(.70, .62, .16, .11, "red", "收货客户章", .07)
    lower = SealRegion(.70, .75, .16, .11, "red", "收货客户章", .07)

    assert len(_merge_split_stamp_fragments([upper, lower])) == 2


def test_page_wide_joined_stamps_are_split_and_roles_reclassified(tmp_path):
    import cv2

    image = np.full((1400, 1000, 3), 255, dtype=np.uint8)
    red = (80, 80, 220)
    cv2.circle(image, (190, 850), 120, red, 18)
    cv2.circle(image, (810, 850), 120, red, 18)
    cv2.line(image, (300, 850), (700, 850), red, 3)
    source = tmp_path / "joined-stamps.jpg"
    assert cv2.imwrite(str(source), image)

    regions = detect_seal_regions(source)

    assert len(regions) == 2
    assert {region.role for region in regions} == {"发货单位章", "收货客户章"}


def test_joined_pair_is_split_even_when_combined_center_looks_like_shipping(tmp_path):
    import cv2

    image = np.full((1400, 1000, 3), 255, dtype=np.uint8)
    red = (80, 80, 220)
    cv2.circle(image, (120, 850), 100, red, 18)
    cv2.circle(image, (720, 850), 100, red, 18)
    cv2.line(image, (220, 850), (620, 850), red, 3)
    source = tmp_path / "left-biased-joined-stamps.jpg"
    assert cv2.imwrite(str(source), image)

    regions = detect_seal_regions(source)

    assert len(regions) == 2
    assert {region.role for region in regions} == {"发货单位章", "收货客户章"}


def test_dense_overlapping_oval_stamps_are_recovered_from_inner_contours(tmp_path):
    import cv2

    image = np.full((1600, 1000, 3), 255, dtype=np.uint8)
    red = (80, 80, 220)
    # Three overlapping customer stamps form one dense connected group after
    # the detector's normal dilation, while their ellipse outlines remain.
    for center in ((620, 900), (790, 980), (680, 1180)):
        cv2.ellipse(image, center, (130, 85), 0, 0, 360, red, 18)
        cv2.putText(image, "TEST", (center[0] - 65, center[1] + 8), cv2.FONT_HERSHEY_SIMPLEX, 1, red, 5)
    source = tmp_path / "overlapping-oval-stamps.jpg"
    assert cv2.imwrite(str(source), image)

    regions = [r for r in detect_seal_regions(source) if r.role == "收货客户章"]

    assert len(regions) >= 2
    assert all(region.width < .36 and region.height < .25 for region in regions)


def test_page_spanning_red_scan_line_does_not_hide_customer_stamp(tmp_path):
    import cv2

    image = np.full((1600, 1000, 3), 255, dtype=np.uint8)
    red = (80, 80, 220)
    # The scanner streak deliberately crosses and connects to the circle.
    cv2.line(image, (760, 0), (760, 1599), red, 2)
    cv2.circle(image, (760, 930), 125, red, 18)
    cv2.putText(
        image, "STAMP", (675, 945), cv2.FONT_HERSHEY_SIMPLEX, 0.8, red, 4
    )
    source = tmp_path / "stamp-with-vertical-scan-line.jpg"
    assert cv2.imwrite(str(source), image)

    regions = [r for r in detect_seal_regions(source) if r.role == "收货客户章"]

    assert len(regions) == 1
    assert regions[0].height < 0.25


def test_customer_stamp_starting_above_footer_cutoff_is_kept_by_center(tmp_path):
    import cv2

    image = np.full((1600, 1000, 3), 255, dtype=np.uint8)
    red = (80, 80, 220)
    # Top begins at 42% of the page, but the stamp center is safely inside the
    # receipt footer. This mirrors customer circles overlapping the total row.
    cv2.circle(image, (770, 800), 128, red, 18)
    source = tmp_path / "high-overlap-customer-stamp.jpg"
    assert cv2.imwrite(str(source), image)

    regions = [r for r in detect_seal_regions(source) if r.role == "收货客户章"]

    assert len(regions) == 1
    assert regions[0].y < .43
