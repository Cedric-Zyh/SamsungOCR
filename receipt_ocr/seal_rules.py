"""Seal reconstruction and business identity rules."""

from __future__ import annotations
import re
from .image_processing import SealRegion
from .parser import compare_seal_text, normalize_text
from .ocr_types import TextObservation
from .recognition_utils import _dedupe


def _apply_code_stamp_business_id(
    seal_check: dict,
    requirement: str,
    recognized_texts: list[str],
    fields: dict[str, str],
) -> dict:
    """Verify an oval code stamp by its independently printed business id.

    Company lettering around a small oval stamp is often unreadable, while
    the straight ``代码：6092851`` line remains clear.  Promote that evidence only
    when SoldToCode and ShipToCode were both read from the form, agree after
    removing leading zeroes, and the red-stamp OCR explicitly labels the same
    6--10 digit value as a code.
    """
    if "代码章" not in normalize_text(requirement):
        return seal_check
    sold = re.sub(r"\D", "", fields.get("SoldToCode", "")).lstrip("0")
    ship = re.sub(r"\D", "", fields.get("ShipToCode", "")).lstrip("0")
    if not sold or sold != ship or not 6 <= len(sold) <= 10:
        return seal_check
    for text in recognized_texts:
        match = re.search(r"(?:站?代码)\s*[:：]?\s*(\d{6,10})", text)
        if not match or match.group(1).lstrip("0") != sold:
            continue
        seal_check.update(
            {
                "recognized": text,
                "score": 1.0,
                "status": "匹配",
                "message": "椭圆代码章编号与 SoldToCode/ShipToCode 一致",
                "confidence": 0.98,
                "reliable": True,
                "code_match": True,
                "expected_code": sold,
                "recognized_code": match.group(1),
                "match_basis": "红章 OCR 代码 + 表单双业务编码",
            }
        )
        break
    return seal_check


def _company_conflict_allows_clipped_prefix_server_recheck(
    requirement: str,
    recognized_texts: list[str],
) -> bool:
    """Allow Server to resolve one narrowly explained local-name conflict.

    A circular crop can clip exactly the first Han glyph while another local
    transform substitutes one internal glyph in an otherwise complete legal
    name (``济南新宇航`` -> ``南新宇航`` / ``济南新字航``).  The complete
    alternate reading must remain a conflict on local evidence alone.  It is
    safe to *ask* the color-only Server audit to resolve it only when another
    local observation contains the exact expected company core minus its
    first glyph.  Missing or differing middle glyphs such as ``大连北华`` vs
    ``大连华/大连允华`` never satisfy this condition.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_index = expected.find(marker)
    if marker_index < 0:
        return False
    company = expected[: marker_index + len(marker)]
    core = company[: -len(marker)]
    if len(core) < 8:
        return False
    clipped = core[1:]
    return any(
        clipped in normalize_text(text)
        for text in recognized_texts
        if normalize_text(text)
    )


def _has_shared_specific_stamp_type(requirement: str, texts: list[str]) -> bool:
    """Return whether local evidence already agrees on a specific stamp type.

    A generic ``专用章`` fragment is too weak.  The longer phrases below are
    useful routing evidence only: they may justify one color-only Server OCR
    audit when the company arc is missing, but the final matcher still needs
    sufficient organization/number evidence before it can become reliable.
    """
    observed = "".join(str(text or "") for text in texts)
    if any(
        expected in requirement and observed_token in observed
        for expected, observed_token in (
            ("维修专用章", "维修专用"),
            ("维修专章", "维修专用"),
            ("售后专用章", "售后专用"),
            ("业务专用章", "业务专用"),
            ("服务专用章", "服务专用"),
            ("检测专用章", "检测专用"),
            ("收货专用章", "收货专用"),
            ("仓储部收货章", "仓储部收货"),
            # Some reviewed forms print ``业务受理`` while the physical
            # customer stamp adds ``专用章`` or a branch number.  The shared
            # four-glyph type is enough to justify one color-only Server
            # audit, but never enough for the final company-name matcher.
            ("业务受理", "业务受理"),
        )
    ):
        return True
    normalized_observed = normalize_text(observed)
    # A pale bilingual rectangular stamp may leave only its independently
    # distinctive Latin brand and the first service glyph in local OCR.
    # These tokens only route a color-only Server audit.  They do not relax
    # the final matcher, which still needs the service stamp type evidence.
    if (
        "三星电子" in requirement
        and "客户服务中心" in requirement
        and "服务专用章" in requirement
        and "samsung" in normalized_observed
        and "服务" in normalized_observed
    ):
        return True
    return bool(
        requirement.startswith("三星电子")
        and "服务中心" in requirement
        and "服务中" in observed
    )


def _strong_unread_colored_stamp_route(
    requirement: str,
    preliminary_score: float,
    max_pixel_ratio: float,
) -> bool:
    """Route a visually strong but locally unread organization stamp.

    Some clean circular stamps are crossed by the black receipt grid. Both
    local engines can consequently return no matching text even though the
    color detector has a dense, unambiguous stamp region. This predicate only
    authorizes one color-only Server audit for known organization-style
    requirements. It never contributes text or relaxes the final matcher.
    """
    normalized = normalize_text(requirement)
    unread_organization = bool(
        (
            (preliminary_score == 0 and max_pixel_ratio >= 0.06)
            # A dense, clean circular stamp can leave one accidental glyph
            # after black grid-line interference.  Treat that near-empty
            # result like the fully unread case only at a much stronger color
            # density.  This is still routing-only: Server must independently
            # satisfy the unchanged final organization matcher.
            or (preliminary_score <= 0.20 and max_pixel_ratio >= 0.20)
        )
        and len(normalized) >= 8
        and any(
            token in requirement
            for token in (
                "服务中心",
                "维修中心",
                "维修部",
                "服务总汇",
                "授权体验店",
                "商店",
            )
        )
    )
    # The reviewed Samsung local-service round stamp is visually dense but
    # often leaves only one place glyph before polar unwrapping.  Permit one
    # color-only Server audit at a narrow low-score boundary.  This is still
    # routing only; the final matcher separately requires brand, place and
    # service-center evidence from one region-level aggregate.
    fragmented_local_service_center = bool(
        re.fullmatch(r"三星电子[\u4e00-\u9fff]{2,6}服务中心", normalized)
        # ``7284653893`` already exposes ``孝/务中心`` fragments and therefore
        # scores 0.462, but still needs the larger recognizer to recover the
        # independently required brand evidence.  This remains a routing
        # ceiling only; the matcher does not accept the partial text.
        and preliminary_score <= 0.50
        and max_pixel_ratio >= 0.08
    )
    return unread_organization or fragmented_local_service_center


def _reconstruct_partitioned_service_organization(
    requirement: str,
    band_variants: dict[str, list[str]],
) -> str:
    """Join 2–3 exact ordered rows from distinct safe seal derivatives.

    No OCR glyph is corrected or copied from the printed requirement: every
    character must occur in exact, non-overlapping OCR fragments. At least one
    fragment must come from an unwrapped band and at least two safe derivative
    labels must contribute.
    """
    expected = normalize_text(requirement)
    if (
        len(expected) < 10
        or not re.fullmatch(r"[\u4e00-\u9fff]+", expected)
        or not expected.endswith("服务中心")
    ):
        return ""
    allowed_prefixes = (
        "圆章展开分带",
        "保留章色旋转对照图",
        "圆章/矩形校正图",
    )
    normalized_by_band = {
        label: {
            normalize_text(text) for text in texts if len(normalize_text(text)) >= 2
        }
        for label, texts in band_variants.items()
        if label.startswith(allowed_prefixes)
    }

    def covers(
        position: int,
        pieces: list[tuple[str, str]],
    ) -> bool:
        if position == len(expected):
            labels = {label for label, _text in pieces}
            return bool(
                2 <= len(pieces) <= 3
                and len(labels) >= 2
                and any(label.startswith("圆章展开分带") for label in labels)
                and any(len(text) >= 4 for _label, text in pieces)
            )
        if len(pieces) >= 3:
            return False
        for label, texts in normalized_by_band.items():
            for text in texts:
                if expected.startswith(text, position) and covers(
                    position + len(text), pieces + [(label, text)]
                ):
                    return True
        return False

    if covers(0, []):
        return expected
    return ""


def _reconstruct_business_acceptance_from_audit(
    requirement: str, audit_texts: list[str]
) -> str:
    """Join exact same-region company fragments without inventing glyphs.

    Circular OCR often returns the place prefix and the rest of the legal
    company on separate rows and in reverse reading order.  Reconstruction is
    limited to a required ``业务受理`` stamp, two independently observed exact
    company substrings, the observed stamp type, and (when printed) the exact
    branch number.  A complete conflicting company vetoes the reconstruction.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if marker_at < 0 or "业务受理" not in expected[marker_at + len(marker) :]:
        return ""
    company = expected[: marker_at + len(marker)]
    normalized = [normalize_text(text) for text in audit_texts if normalize_text(text)]
    business_number = re.search(r"业务受理(\d+)", expected)
    observed_type = "业务受理" + (business_number.group(1) if business_number else "")
    if not any(observed_type in text for text in normalized):
        return ""
    if compare_seal_text(requirement, normalized).get("company_conflict"):
        return ""
    if any(company in text for text in normalized):
        return ""
    for split in range(2, len(company) - 7):
        left, right = company[:split], company[split:]
        left_rows = {index for index, text in enumerate(normalized) if left == text}
        right_rows = {index for index, text in enumerate(normalized) if right == text}
        if (
            left_rows
            and right_rows
            and any(a != b for a in left_rows for b in right_rows)
        ):
            return company + observed_type
    return ""


def _reconstruct_exact_company_stamp_from_region(
    requirement: str, region_texts: list[str]
) -> str:
    """Join exact company/type rows from one color-safe OCR seal region.

    A clean round seal can put the legal company on its arc and the stamp type
    in the centre, so Paddle returns them as separate rows.  Reconstruction is
    deliberately limited to an exact standalone legal organization (including
    an explicitly observed branch), an exact specific type row, and an
    independently exact short identifier when the printed requirement has
    one.  Branch wording or missing type glyphs are never copied from the
    requirement.
    """
    expected = normalize_text(requirement)
    specific_types = (
        "售后业务专用章",
        "手机售后专用章",
        "售后服务专用章",
        "业务受理专用章",
        "检测专用章",
        "业务专用章",
        "维修专用章",
        "服务专用章",
        "收货专用章",
        "仓储部收货章",
    )
    type_match = next(
        (
            (value, match)
            for value in specific_types
            if (
                match := re.search(
                    re.escape(value) + r"(?P<identifier>\d{0,3})$",
                    expected,
                )
            )
            is not None
        ),
        None,
    )
    if type_match is None:
        return ""
    base_type, match = type_match
    legal_markers = list(
        re.finditer(r"有限责任公司|有限公司|分公司", expected[: match.start()])
    )
    if not legal_markers:
        return ""
    legal_end = legal_markers[-1].end()
    organization = expected[:legal_end]
    type_prefix = expected[legal_end : match.start()]
    # Branch stamps sometimes put a short directional/brand prefix such as
    # ``北`` or ``三星`` on the type arc.  It must be observed as part of the
    # exact same-region type row; longer narrative text is not reclassified.
    if type_prefix and not re.fullmatch(r"[\u4e00-\u9fff]{1,3}", type_prefix):
        return ""
    stamp_type = type_prefix + base_type
    identifier = match.group("identifier")
    normalized = [normalize_text(text) for text in region_texts if normalize_text(text)]
    organization_seen = any(
        text == organization
        or (
            text.startswith(organization)
            and re.fullmatch(r"\d{6,16}", text[len(organization) :])
        )
        for text in normalized
    )
    if not organization_seen or stamp_type not in normalized:
        return ""
    if compare_seal_text(requirement, normalized).get("company_conflict"):
        return ""
    if identifier and identifier not in normalized:
        return ""
    return organization + stamp_type + identifier


def _reconstruct_one_error_round_type_band(
    requirement: str,
    existing_region_texts: list[str],
    type_band_rows: list[TextObservation],
) -> str:
    """Repair one high-confidence glyph in a focused round-stamp type row.

    This is narrower than general fuzzy stamp matching.  The exact legal
    organization must already exist in independent same-region evidence, the
    color-only lower band must retain the literal ``专用章`` ending, and its
    type must have the same length with exactly one substituted Han glyph.
    Missing characters, insertions, identifiers, company conflicts, or a
    recognition score below 75% remain manual-review cases.
    """
    expected = normalize_text(requirement)
    specific_types = (
        "售后业务专用章",
        "手机售后专用章",
        "售后服务专用章",
        "业务受理专用章",
        "检测专用章",
        "业务专用章",
        "维修专用章",
        "服务专用章",
        "收货专用章",
    )
    stamp_type = next(
        (value for value in specific_types if expected.endswith(value)), ""
    )
    if not stamp_type:
        return ""
    organization = expected[: -len(stamp_type)]
    if not organization.endswith(("有限公司", "有限责任公司", "分公司")):
        return ""
    normalized_existing = [
        normalize_text(text) for text in existing_region_texts if normalize_text(text)
    ]
    organization_seen = any(
        text == organization
        or (
            text.startswith(organization)
            and re.fullmatch(r"\d{6,16}", text[len(organization) :])
        )
        for text in normalized_existing
    )
    complete_organizations = {
        match.group("organization")
        for text in normalized_existing
        if len(text) >= len(organization) - 2
        and len(text) <= len(organization) + 16
        and (
            match := re.fullmatch(
                r"(?P<organization>.+?(?:有限责任公司|有限公司|分公司))"
                r"(?:\d{6,16})?",
                text,
            )
        )
        is not None
    }
    if (
        not organization_seen
        or complete_organizations - {organization}
        or compare_seal_text(requirement, normalized_existing).get("company_conflict")
    ):
        return ""
    for row in type_band_rows:
        observed = normalize_text(row.text)
        if (
            float(row.confidence) >= 0.75
            and observed.endswith("专用章")
            and len(observed) == len(stamp_type)
            and sum(
                expected_char != observed_char
                for expected_char, observed_char in zip(stamp_type, observed)
            )
            == 1
        ):
            return organization + stamp_type
    return ""


def _reconstruct_business_acceptance_from_mobile_bands(
    requirement: str,
    mobile_band_texts: list[str],
    existing_region_texts: list[str],
) -> str:
    """Resolve one company glyph conflict with an exact independent row.

    This route never copies the printed ``专用章`` suffix.  It returns only
    the exact legal company read by Mobile from an isolated rectangular band
    and the independently observed ``业务受理`` row from the same color-safe
    stamp region.  Any alternate complete company in the Mobile bands vetoes
    the reconstruction.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if marker_at < 0:
        return ""
    company = expected[: marker_at + len(marker)]
    suffix = expected[marker_at + len(marker) :]
    if "业务受理" not in suffix:
        return ""
    mobile = [
        normalize_text(text) for text in mobile_band_texts if normalize_text(text)
    ]
    if company not in mobile:
        return ""
    if any(marker in text and text != company for text in mobile):
        return ""
    existing = [
        normalize_text(text) for text in existing_region_texts if normalize_text(text)
    ]
    branch = re.search(r"业务受理(\d{1,3})", suffix)
    observed_type = "业务受理" + (branch.group(1) if branch else "")
    if not any(
        text == observed_type or (not branch and text == "业务受理专用章")
        for text in existing + mobile
    ):
        return ""
    return company + observed_type


def _shared_long_organization_suffix(
    requirement: str,
    mobile_texts: list[str],
    server_texts: list[str],
) -> str:
    """Return only a long required suffix independently read by both models.

    The missing place prefix is never copied from the printed requirement.
    Both OCR models must contain the same exact required suffix, covering at
    least 70% of the pure organization name and omitting no more than three
    leading Han glyphs.
    """
    expected = normalize_text(requirement)
    if (
        len(expected) < 10
        or not re.fullmatch(r"[\u4e00-\u9fff]+", expected)
        or not expected.endswith(("商店", "维修站", "服务中心"))
    ):
        return ""
    mobile = [normalize_text(text) for text in mobile_texts if normalize_text(text)]
    server = [normalize_text(text) for text in server_texts if normalize_text(text)]
    minimum = max(8, round(len(expected) * 0.70))
    max_omitted = min(3, len(expected) - minimum)
    for omitted in range(max_omitted + 1):
        suffix = expected[omitted:]
        if any(suffix in text for text in mobile) and any(
            suffix in text for text in server
        ):
            return suffix
    return ""


def _region_overlap_over_smaller(first: SealRegion, second: SealRegion) -> float:
    """Return intersection area divided by the smaller seal-region area."""
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min(
        first.width * first.height,
        second.width * second.height,
    )
    return intersection / smaller if smaller > 0 else 0.0


def _reconstruct_overlapping_repair_stamp(
    requirement: str,
    audited_regions: list[dict],
) -> str:
    """Join exact OCR fragments from two strongly overlapping customer seals.

    The reviewed receipt can contain two customer stamps applied on top of one
    another.  Color segmentation then returns two overlapping boxes: one keeps
    the start of the legal company and the explicit stamp type, while the other
    keeps the remainder of the same company arc.  Reconstruction is allowed
    only for the printed ``维修专章`` template, requires an independently read
    ``维修专用章`` token, and joins two exact complementary company fragments
    from different overlapping regions.  No missing glyph is copied into the
    output, and any complete-company conflict remains a hard veto.
    """
    expected = normalize_text(requirement)
    marker = "有限公司"
    marker_at = expected.find(marker)
    if marker_at < 0 or expected[marker_at + len(marker) :] not in {
        "维修专章",
        "维修专用章",
    }:
        return ""
    company = expected[: marker_at + len(marker)]
    for first_index, first in enumerate(audited_regions):
        first_region = first.get("region")
        if not isinstance(first_region, SealRegion):
            continue
        first_texts = {
            normalize_text(text)
            for text in first.get("texts", [])
            if normalize_text(text)
        }
        for second in audited_regions[first_index + 1 :]:
            second_region = second.get("region")
            if not isinstance(second_region, SealRegion):
                continue
            if (
                first_region.role != "收货客户章"
                or second_region.role != "收货客户章"
                or first_region.color != second_region.color
                or _region_overlap_over_smaller(first_region, second_region) < 0.35
            ):
                continue
            second_texts = {
                normalize_text(text)
                for text in second.get("texts", [])
                if normalize_text(text)
            }
            all_texts = sorted(first_texts | second_texts)
            if "维修专用章" not in all_texts:
                continue
            if compare_seal_text(requirement, all_texts).get("company_conflict"):
                continue
            for split in range(2, len(company) - 1):
                left, right = company[:split], company[split:]
                if (left in first_texts and right in second_texts) or (
                    left in second_texts and right in first_texts
                ):
                    return left + right + "维修专用章"
    return ""


def combine_region_texts(values: list[str]) -> str:
    """Fuse OCR fragments only inside one detected seal region."""
    return "".join(_dedupe(values))


def _extract_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        output: list[str] = []
        for item in value.values():
            output.extend(_extract_strings(item))
        return output
    if isinstance(value, list):
        output = []
        for item in value:
            output.extend(_extract_strings(item))
        return output
    return []
