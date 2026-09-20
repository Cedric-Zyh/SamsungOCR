"""Compatibility facade; stage execution lives in pipeline and stage modules."""

from .execution import recognition_run
from .seal_api import SealApiClient
from .pipeline import run_legacy, execute_stage
from .decision import decide_overall
from .date_crops import (
    _recognize_receipt_date,
)
from .date_evidence import (
    _adaptive_day_slot_confirms_value,
    _collect_business_rejected_date_evidence,
    _conflicting_receipt_dates,
    _cross_model_far_lower_complementary_date,
    _cross_model_far_lower_strict_date,
    _cross_model_max_channel_mismatch_date,
    _cross_model_max_channel_required_with_truncated_conflict,
    _cross_model_server_strict_component_date,
    _cross_model_slot_required_date,
    _cross_year_nondestructive_consensus_from_artifacts,
    _cross_year_strict_consensus_from_artifacts,
    _date_component_consensus_from_artifacts,
    _date_slot_white_canvas,
    _day_slot_confirms_value,
    _explicit_trailing_numeric_month_day,
    _find_confirmed_far_lower_date,
    _find_low_confidence_date_audit,
    _is_date_audit_only_preprocessing,
    _max_channel_truncated_mismatch_candidate,
    _missing_month_day_component_prefilter_from_artifacts,
    _missing_year_separator_consensus_from_artifacts,
    _mobile_component_supports_date,
    _needs_low_confidence_date_audit,
    _parse_adaptive_day_slot,
    _parse_compact_full_date_audit_candidate,
    _parse_date_slot_digit,
    _parse_date_slot_month_day,
    _parse_date_slot_year,
    _parse_full_year_missing_month_day,
    _parse_full_year_month_day_audit,
    _parse_missing_year_separator_full_date,
    _parse_partial_year_month_day_audit,
    _parse_server_audit_candidate,
    _partial_year_day_before_audit_from_artifacts,
    _partial_year_missing_month_business_consensus_from_artifacts,
    _partial_year_month_day,
    _recognize_adaptive_day_slot_variants,
    _recognize_day_slot_variants,
    _reject_date_before_creation,
    _repeated_server_required_date_from_artifacts,
    _repeated_strict_date_across_variants,
    _required_month_slot_conflict_consensus_from_artifacts,
    _required_month_slot_conflict_prefilter_from_artifacts,
    _same_geometry_missing_month_consensus_from_artifacts,
    _same_geometry_partial_year_required_consensus_from_artifacts,
    _save_date_line_crop,
    _save_date_slot_views,
    _save_date_upper_line_crop,
    _save_right_padded_date_line,
    _select_display_only_date_audit_rows,
    _server_cross_geometry_date_with_mobile_components_from_artifacts,
    _server_cross_geometry_strict_date_from_artifacts,
    _server_mobile_dominant_date_from_artifacts,
    _server_strict_component_consensus_from_artifacts,
    _single_server_strict_truncated_day_candidate,
    _trailing_numeric_month_day,
    _unanimous_month_day_business_year_consensus_from_artifacts,
    _unique_server_mobile_otsu_candidate,
    _white_day_conflict_audit_candidate,
    _white_day_conflict_prefilter_from_artifacts,
    _year_month_prefix,
)
from .document_layout import (
    _exclude_printed_footer_rows_from_seal_context,
    _find_signature_requirement_row,
)
from .field_rules import (
    _is_neighboring_label_misread_as_receipt_note,
    _prefer_detail_field,
    _prefer_detail_requirement,
    _recover_confirmed_template_note,
    _recover_signature_requirement,
    _trim_signature_requirement_candidate,
)
from .product_rules import (
    _fuse_product_descriptions,
    _fuse_product_material,
    _recover_missing_product_grades,
)
from .recognition_safety import (
    _apply_single_paddle_safety,
    _ocr_model_config,
)
from .recognition_utils import (
    _dedupe,
)
from .seal_crops import (
    _recognize_local_seals,
)
from .seal_rules import (
    _apply_code_stamp_business_id,
    _company_conflict_allows_clipped_prefix_server_recheck,
    _extract_strings,
    _has_shared_specific_stamp_type,
    _reconstruct_business_acceptance_from_audit,
    _reconstruct_business_acceptance_from_mobile_bands,
    _reconstruct_exact_company_stamp_from_region,
    _reconstruct_one_error_round_type_band,
    _reconstruct_overlapping_repair_stamp,
    _reconstruct_partitioned_service_organization,
    _region_overlap_over_smaller,
    _shared_long_organization_suffix,
    _strong_unread_colored_stamp_route,
    combine_region_texts,
)


class ReceiptAnalyzer:
    def __init__(self):
        self.seal_api = SealApiClient()

    @recognition_run
    def analyze(self, image_path, preview_path=None, **options):
        return run_legacy(self, image_path, preview_path, **options)

    def run_stage(self, context, stage, request):
        return execute_stage(self, context, stage, request)

    def _recognize_receipt_date(self, *args, **kwargs):
        return _recognize_receipt_date(*args, **kwargs)

    def _recognize_local_seals(self, *args, **kwargs):
        return _recognize_local_seals(*args, **kwargs)
