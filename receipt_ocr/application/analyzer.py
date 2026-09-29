"""Application entry point for receipt recognition."""

from ..runtime.execution import recognition_run
from receipt_ocr.recognition.seal.api import SealApiClient
from .pipeline import execute_stage, run_legacy


class ReceiptAnalyzer:
    """Coordinate one recognition run and expose provider callbacks to stages."""

    def __init__(self):
        self.seal_api = SealApiClient()

    @recognition_run
    def analyze(self, image_path, preview_path=None, **options):
        return run_legacy(self, image_path, preview_path, **options)

    def run_stage(self, context, stage, request):
        return execute_stage(self, context, stage, request)

    def _recognize_receipt_date(self, *args, **kwargs):
        from receipt_ocr.recognition.date.api import recognize_receipt_date
        return recognize_receipt_date(*args, **kwargs)

    def _recognize_local_seals(self, *args, **kwargs):
        from receipt_ocr.recognition.seal.workflow import recognize_local_seals
        return recognize_local_seals(*args, **kwargs)

# Stable application-level aliases for evidence helpers used by integrations and
# analysis tools. Implementations stay inside their functional vertical slices.
from receipt_ocr.domain.decision import decide_overall
from receipt_ocr.domain.documents.layout import (
    _exclude_printed_footer_rows_from_seal_context,
    _find_signature_requirement_row,
)
from receipt_ocr.domain.fields.rules import (
    _is_neighboring_label_misread_as_receipt_note,
    _prefer_detail_field,
    _prefer_detail_requirement,
    _recover_confirmed_template_note,
    _recover_signature_requirement,
)
from receipt_ocr.domain.products.rules import (
    _fuse_product_descriptions,
    _fuse_product_material,
    _recover_missing_product_grades,
)
from receipt_ocr.runtime.safety import _apply_single_paddle_safety, _ocr_model_config
from receipt_ocr.recognition.seal.decision import (
    _apply_code_stamp_business_id,
    _company_conflict_allows_clipped_prefix_secondary_recheck,
    _reconstruct_business_acceptance_from_secondary,
    _has_shared_specific_stamp_type,
    _reconstruct_business_acceptance_from_line_bands,
    _reconstruct_exact_company_stamp_from_region,
    _reconstruct_one_error_round_type_band,
    _reconstruct_overlapping_repair_stamp,
    _reconstruct_partitioned_service_organization,
    _shared_long_organization_suffix,
    _strong_unread_colored_stamp_route,
    combine_region_texts,
)

# Kept as a compatibility export for analysis tools; seal code uses the
# provider-neutral secondary-read name.
_company_conflict_allows_clipped_prefix_server_recheck = (
    _company_conflict_allows_clipped_prefix_secondary_recheck
)
_reconstruct_business_acceptance_from_audit = _reconstruct_business_acceptance_from_secondary
_reconstruct_business_acceptance_from_mobile_bands = _reconstruct_business_acceptance_from_line_bands
