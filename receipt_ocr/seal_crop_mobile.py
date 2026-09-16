"""Independent Mobile band checks for conflicting local seal companies."""

from __future__ import annotations

from pathlib import Path

from .image_processing import save_rectangular_seal_bands
from .ocr_backends import backend_label, recognize_text
from .parser import compare_seal_text, normalize_text
from .recognition_utils import _dedupe
from .seal_rules import _reconstruct_business_acceptance_from_mobile_bands
from .seal_crop_types import SealCropRequest, SealEvidenceCollection, SealAuditRoute


def _resolve_mobile_company_conflict(
    request: SealCropRequest,
    collection: SealEvidenceCollection,
    route: SealAuditRoute,
) -> None:
    """Try the guarded Mobile band route before planning any Server audit."""
    # Use the larger recognition model only when regular local
    # evidence is still unable to form a reliable stamp conclusion.
    # It receives color-isolated derivatives only, never the black
    # printed requirement row, which prevents circular self-matching.
    route.preliminary = compare_seal_text(request.requirement, _dedupe(collection.texts))
    route.preliminary_company_conflict = bool(route.preliminary.get("company_conflict"))
    # A reviewed rectangular business-acceptance stamp has one
    # company glyph read differently by Vision/full-image Mobile, but
    # an isolated horizontal Mobile band reads the exact legal name.
    # Use this only as a cross-model conflict resolver on macOS
    # Hybrid. Pure Paddle/Windows remains single-model and cannot
    # self-certify a date or seal conclusion.
    conflict_mobile_band_resolution = ""
    if (
        route.preliminary_company_conflict
        and request.ocr_backend == "vision"
        and request.secondary_ocr_backend == "paddle"
        and "业务受理" in normalize_text(request.requirement)
        and collection.server_candidates
    ):
        ranked_mobile_candidates = sorted(
            collection.server_candidates,
            key=lambda candidate: compare_seal_text(
                request.requirement, candidate["evidence"]
            ).get("score", 0),
            reverse=True,
        )
        mobile_candidate = ranked_mobile_candidates[0]
        if mobile_candidate["rectangular"]:
            mobile_band_paths: list[Path] = []
            mobile_band_texts: list[str] = []
            mobile_band_variants: list[dict] = []
            try:
                mobile_band_paths = save_rectangular_seal_bands(
                    mobile_candidate["unwrapped"],
                    Path(mobile_candidate["unwrapped"]).with_name(
                        f"seal-{mobile_candidate['index']}-"
                        "rectangular-company-band"
                    ),
                )
            except Exception:
                mobile_band_paths = []
            for band_index, band_path in enumerate(mobile_band_paths, start=1):
                current_texts: list[str] = []
                try:
                    current_texts = [
                        row.text
                        for row in recognize_text(
                            band_path,
                            backend="paddle",
                            min_text_height=0.012,
                        )
                        if row.text
                    ]
                except Exception:
                    current_texts = []
                mobile_band_texts.extend(current_texts)
                mobile_band_variants.append(
                    {
                        "preprocessing": f"矩形章横向分带 {band_index}",
                        "ocr_texts": current_texts,
                    }
                )
            mobile_band_texts = _dedupe(mobile_band_texts)
            conflict_mobile_band_resolution = (
                _reconstruct_business_acceptance_from_mobile_bands(
                    request.requirement,
                    mobile_band_texts,
                    mobile_candidate["evidence"],
                )
            )
            if conflict_mobile_band_resolution:
                collection.texts.append(conflict_mobile_band_resolution)
                route.preliminary = compare_seal_text(request.requirement, _dedupe(collection.texts))
                route.preliminary_company_conflict = bool(
                    route.preliminary.get("company_conflict")
                )
            if mobile_candidate["index"] < len(collection.artifacts):
                collection.artifacts[mobile_candidate["index"]].update(
                    conflict_mobile_band_backend=(backend_label("paddle")),
                    conflict_mobile_band_text=" | ".join(mobile_band_texts),
                    conflict_mobile_band_variants=(mobile_band_variants),
                    conflict_mobile_band_urls=[
                        f"{request.artifact_url_prefix.rstrip('/')}/seals/{path.name}"
                        for path in mobile_band_paths
                        if request.artifact_dir and path.is_file()
                    ],
                    conflict_mobile_band_resolution=(
                        conflict_mobile_band_resolution
                    ),
                    conflict_mobile_band_acceptance_note=(
                        "Mobile 独立分带完整公司 + 同章区业务受理，"
                        "允许解决常规模型单字公司冲突"
                        if conflict_mobile_band_resolution
                        else "未形成完整且无其他公司的 Mobile 分带证据，"
                        "保持公司冲突待复核"
                    ),
                )
