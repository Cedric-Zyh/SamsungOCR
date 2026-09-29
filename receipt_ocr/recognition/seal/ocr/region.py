"""OCR stage for prepared seal variants.

The functions in this module intentionally receive prepared image paths from
``preprocess.regions``.  They do not crop, rotate, normalize, or classify a
stamp; they only read the variants and attach observations to the region
contract.
"""

from __future__ import annotations

from .providers import profile_for
from receipt_ocr.recognition.seal.contracts import RegionEvidence, SealRequest
from receipt_ocr.recognition.seal.ocr.interface import read_line, read_text


def read_primary_region(request: SealRequest, evidence: RegionEvidence) -> None:
    """Read the primary provider from every prepared variant."""

    if evidence.round_type_band is not None and evidence.round_type_band.is_file():
        try:
            rows = read_line(
                evidence.round_type_band,
                provider=request.ocr_backend,
            )
            evidence.round_type_band_texts = [row.text for row in rows if row.text]
            detected = evidence.orientation.get("type_row_texts", [])
            anchor = detected[0] if detected else ""
            suffixes = detected[1:]
            if anchor and anchor not in evidence.round_type_band_texts:
                evidence.round_type_band_texts.insert(0, anchor)
            if anchor in evidence.round_type_band_texts:
                position = evidence.round_type_band_texts.index(anchor)
                suffix = "".join(
                    value
                    for value in suffixes
                    if value and value not in evidence.round_type_band_texts
                )
                if suffix:
                    evidence.round_type_band_texts[position] = anchor + suffix
            else:
                evidence.round_type_band_texts.extend(
                    value
                    for value in suffixes
                    if value and value not in evidence.round_type_band_texts
                )
            evidence.region_texts.extend(evidence.round_type_band_texts)
        except Exception:
            evidence.round_type_band_texts = []

    if evidence.code_line is not None and evidence.code_line.is_file():
        try:
            if profile_for(request.ocr_backend).supports_lines:
                rows = read_line(evidence.code_line, provider=request.ocr_backend)
            else:
                rows = read_text(
                    evidence.code_line,
                    backend=request.ocr_backend,
                    min_text_height=0.025,
                )
            evidence.code_line_texts = [row.text for row in rows if row.text]
            evidence.region_texts.extend(evidence.code_line_texts)
        except Exception:
            evidence.code_line_texts = []

    try:
        rows = read_text(
            evidence.color_isolated,
            backend=request.ocr_backend,
            min_text_height=0.012,
        )
        evidence.color_isolated_texts = [row.text for row in rows if row.text]
    except Exception:
        evidence.color_isolated_texts = []
    evidence.region_texts.extend(evidence.color_isolated_texts)

    evidence.oriented_texts = []
    if (
        evidence.orientation_anchor_text
        and float(evidence.orientation.get("confidence", 0.0) or 0.0) >= 0.70
    ):
        evidence.oriented_texts.append(evidence.orientation_anchor_text)
    if evidence.color_isolated_oriented is not None:
        evidence.oriented_texts.extend(evidence.round_type_band_texts)
    evidence.region_texts.extend(evidence.oriented_texts)

    try:
        if profile_for(request.ocr_backend).supports_lines and not evidence.rectangular:
            paths = evidence.unwrapped_bands
            rows = []
            for path in paths:
                rows.extend(
                    read_line(
                        path,
                        provider=request.ocr_backend,
                    )
                )
        else:
            rows = read_text(
                evidence.unwrapped,
                backend=request.ocr_backend,
                min_text_height=0.05,
            )
        evidence.unwrap_texts = [row.text for row in rows if row.text]
    except Exception:
        evidence.unwrap_texts = []
    evidence.region_texts.extend(evidence.unwrap_texts)

    if evidence.rotated is not None and evidence.rotated.is_file():
        try:
            rows = read_text(
                evidence.rotated,
                backend=request.ocr_backend,
                min_text_height=0.012,
            )
            evidence.rotated_texts = [row.text for row in rows if row.text]
        except Exception:
            evidence.rotated_texts = []
        evidence.region_texts.extend(evidence.rotated_texts)


def read_secondary_region(request: SealRequest, evidence: RegionEvidence) -> None:
    """Read optional secondary provider variants without changing geometry."""

    provider = request.secondary_ocr_backend
    if not provider:
        return

    if evidence.original is not None and evidence.original.is_file():
        try:
            evidence.secondary_original_texts = [
                row.text
                for row in read_text(
                    evidence.original,
                    backend=provider,
                    min_text_height=0.012,
                )
                if row.text
            ]
        except Exception:
            evidence.secondary_original_texts = []

    if evidence.rotated is not None and evidence.rotated.is_file() and evidence.rectangular:
        try:
            evidence.secondary_rotated_texts = [
                row.text
                for row in read_text(
                    evidence.rotated,
                    backend=provider,
                    min_text_height=0.012,
                )
                if row.text
            ]
        except Exception:
            evidence.secondary_rotated_texts = []

    try:
        evidence.secondary_color_isolated_texts = [
            row.text
            for row in read_text(
                evidence.color_isolated,
                backend=provider,
                min_text_height=0.012,
            )
            if row.text
        ]
    except Exception:
        evidence.secondary_color_isolated_texts = []

    try:
        if profile_for(provider).supports_lines and not evidence.rectangular:
            paths = evidence.unwrapped_bands
            rows = []
            for path in paths:
                rows.extend(
                    read_line(path, provider=provider)
                )
        else:
            rows = read_text(
                evidence.unwrapped,
                backend=provider,
                min_text_height=0.04,
            )
        evidence.secondary_unwrap_texts = [row.text for row in rows if row.text]
    except Exception:
        evidence.secondary_unwrap_texts = []

    if evidence.code_line is not None and evidence.code_line.is_file():
        try:
            if profile_for(provider).supports_lines:
                rows = read_line(evidence.code_line, provider=provider)
            else:
                rows = read_text(
                    evidence.code_line,
                    backend=provider,
                    min_text_height=0.025,
                )
            evidence.secondary_code_line_texts = [row.text for row in rows if row.text]
        except Exception:
            evidence.secondary_code_line_texts = []

    # The original crop is safe for matching only when it lies below the
    # printed footer. It is still retained as evidence in every other case.
    if evidence.original_safe_for_matching:
        evidence.region_texts.extend(evidence.secondary_original_texts)
    evidence.region_texts.extend(evidence.secondary_crop_texts)
    evidence.region_texts.extend(evidence.secondary_color_isolated_texts)
    evidence.region_texts.extend(evidence.secondary_unwrap_texts)
    evidence.region_texts.extend(evidence.secondary_rotated_texts)
    evidence.region_texts.extend(evidence.secondary_code_line_texts)


__all__ = ["read_primary_region", "read_secondary_region"]
