"""Combine regional readings and build traceable result artifacts."""
from receipt_ocr.imaging.contracts import SealRegion
from receipt_ocr.providers.catalog import backend_label
from receipt_ocr.runtime.utils import _dedupe
from ..contracts import OcrRead, SealRequest, SealReadCollection, RegionEvidence
from .rules import _reconstruct_exact_company_stamp_from_region, combine_region_texts

def _record_region_evidence(
    request: SealRequest,
    collection: SealReadCollection,
    evidence: RegionEvidence,
    index: int,
    region: SealRegion,
) -> None:
    """Record regional text, artifacts and candidates for the later evidence."""
    evidence.same_region_reconstructed_text = (
        _reconstruct_exact_company_stamp_from_region(request.requirement, evidence.region_texts)
    )
    if evidence.same_region_reconstructed_text:
        evidence.region_texts.append(evidence.same_region_reconstructed_text)
    evidence.combined_text = combine_region_texts(evidence.region_texts)
    collection.prepared.append(evidence.prepared)
    collection.reads.extend(
        OcrRead(
            text=text,
            provider=request.ocr_backend,
            variant="region-aggregate",
            source=evidence.prepared.variants[0].path
            if evidence.prepared.variants
            else None,
        )
        for text in evidence.region_texts
        if text
    )
    collection.texts.extend(evidence.region_texts)
    if len(evidence.region_texts) >= 2 and evidence.combined_text:
        collection.texts.append(evidence.combined_text)
    if request.artifact_dir:
        prefix = request.artifact_url_prefix.rstrip("/")
        collection.artifacts.append(
            {
                "index": index,
                "color": region.color,
                "role": region.role,
                "shape": (
                    "矩形"
                    if evidence.rectangular
                    else "椭圆" if evidence.elliptical else "圆形"
                ),
                "qingtong_shape": region.qingtong_cls or "",
                "shape_source": "本地章色轮廓校验（清瞳章型仅作提示）",
                "ocr_backend": backend_label(request.ocr_backend),
                "secondary_ocr_backend": (
                    backend_label(request.secondary_ocr_backend)
                    if request.secondary_ocr_backend
                    else ""
                ),
                "original_url": f"{prefix}/seals/{evidence.original.name}",
                "isolated_url": f"{prefix}/seals/{evidence.isolated.name}",
                "color_isolated_url": f"{prefix}/seals/{evidence.color_isolated.name}",
                "color_isolated_oriented_url": (
                    f"{prefix}/seals/{evidence.color_isolated_oriented.name}"
                    if evidence.color_isolated_oriented is not None
                    and evidence.color_isolated_oriented.is_file()
                    else ""
                ),
                "ellipse_normalized_url": (
                    f"{prefix}/seals/{evidence.ellipse_normalized.name}"
                    if evidence.ellipse_normalized is not None
                    and evidence.ellipse_normalized.is_file()
                    else ""
                ),
                "orientation": evidence.orientation,
                "orientation_anchor_text": evidence.orientation_anchor_text,
                "round_type_band_url": (
                    f"{prefix}/seals/{evidence.round_type_band.name}"
                    if evidence.round_type_band is not None
                    and evidence.round_type_band.is_file()
                    else ""
                ),
                "round_type_band_text": " | ".join(evidence.round_type_band_texts),
                "code_line_url": (
                    f"{prefix}/seals/{evidence.code_line.name}"
                    if evidence.code_line is not None and evidence.code_line.is_file()
                    else ""
                ),
                "unwrapped_url": f"{prefix}/seals/{evidence.unwrapped.name}",
                "unwrapped_rotated_url": (
                    f"{prefix}/seals/{evidence.unwrapped_rotated.name}"
                    if evidence.unwrapped_rotated is not None
                    and evidence.unwrapped_rotated.is_file()
                    else ""
                ),
                "color_isolated_rotations_url": (
                    f"{prefix}/seals/{evidence.color_isolated_rotations.name}"
                    if evidence.color_isolated_rotations is not None
                    and evidence.color_isolated_rotations.is_file()
                    else ""
                ),
                "rotated_url": (
                    f"{prefix}/seals/{evidence.rotated.name}"
                    if evidence.rectangular and evidence.rotated.is_file()
                    else ""
                ),
                "page_text": evidence.whole_text,
                "isolated_text": evidence.crop_text,
                "color_isolated_text": " | ".join(evidence.color_isolated_texts),
                "color_isolated_oriented_text": " | ".join(evidence.oriented_texts),
                "code_line_text": " | ".join(evidence.code_line_texts),
                "unwrapped_text": " | ".join(evidence.unwrap_texts),
                "rotated_text": " | ".join(evidence.rotated_texts),
                "secondary_original_text": " | ".join(evidence.secondary_original_texts),
                "secondary_original_used_for_matching": evidence.original_safe_for_matching,
                "secondary_isolated_text": " | ".join(evidence.secondary_crop_texts),
                "secondary_color_isolated_text": " | ".join(
                    evidence.secondary_color_isolated_texts
                ),
                "secondary_unwrapped_text": " | ".join(evidence.secondary_unwrap_texts),
                "secondary_rotated_text": " | ".join(evidence.secondary_rotated_texts),
                "secondary_code_line_text": " | ".join(
                    evidence.secondary_code_line_texts
                ),
                "same_region_reconstructed_text": (
                    evidence.same_region_reconstructed_text
                ),
                "combined_text": evidence.combined_text,
            }
        )
    collection.candidates.append(
        {
            "index": index,
            "region": region,
            "pixel_ratio": float(region.pixel_ratio),
            "isolated": evidence.isolated,
            "color_isolated": evidence.color_isolated,
            "color_isolated_oriented": evidence.color_isolated_oriented,
            "ellipse_normalized": evidence.ellipse_normalized,
            "orientation": evidence.orientation,
            "ring_type_mask_boxes": (
                [evidence.orientation["ring_type_mask_box"]]
                if evidence.orientation.get("ring_type_mask_box") else []
            ),
            "code_line": evidence.code_line,
            "unwrapped": evidence.unwrapped,
            "unwrapped_rotated": evidence.unwrapped_rotated,
            "color_isolated_rotations": evidence.color_isolated_rotations,
            "rotated": evidence.rotated if evidence.rectangular and evidence.rotated.is_file() else None,
            "rectangular": evidence.rectangular,
            "elliptical": evidence.elliptical,
            "shape": (
                "矩形"
                if evidence.rectangular
                else "椭圆" if evidence.elliptical else "圆形"
            ),
            "qingtong_shape": region.qingtong_cls or "",
            "evidence": _dedupe(
                evidence.region_texts + ([evidence.combined_text] if evidence.combined_text else [])
            ),
        }
    )
