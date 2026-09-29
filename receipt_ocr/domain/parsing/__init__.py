"""Receipt parsing domain services.

These exports are the domain parsing API.  Web handlers and recognition
stages depend on this package instead of importing parser implementation files
or transport compatibility modules.
"""

from __future__ import annotations

import re as re
from datetime import date as date
from difflib import SequenceMatcher as SequenceMatcher
from typing import Iterable as Iterable
from urllib.parse import parse_qs as parse_qs, urlparse as urlparse

from ..documents.types import continuation_row_evidence as continuation_row_evidence
from ..ocr import TextObservation as TextObservation
from .parsing_constants import (
    DATE_PATTERN as DATE_PATTERN,
    FIELD_LABELS as FIELD_LABELS,
    FIXED_FIELD_PHRASES as FIXED_FIELD_PHRASES,
    FIXED_PHRASE_MIN_SIMILARITY as FIXED_PHRASE_MIN_SIMILARITY,
    FIXED_PHRASE_MIN_SIMILARITY_BY_FIELD as FIXED_PHRASE_MIN_SIMILARITY_BY_FIELD,
    LOW_CONFIDENCE_THRESHOLD as LOW_CONFIDENCE_THRESHOLD,
    PRODUCT_COLUMNS as PRODUCT_COLUMNS,
    PRODUCT_COLUMN_RANGES as PRODUCT_COLUMN_RANGES,
    PRODUCT_REQUIRED_COLUMNS as PRODUCT_REQUIRED_COLUMNS,
    VERIFIED_CUSTOMER_BY_SHIP_TO_CODE as VERIFIED_CUSTOMER_BY_SHIP_TO_CODE,
    VERIFIED_CUSTOMER_BY_STATION as VERIFIED_CUSTOMER_BY_STATION,
    VERIFIED_EAN_BY_MATERIAL as VERIFIED_EAN_BY_MATERIAL,
    VERIFIED_MATERIAL_BY_EAN as VERIFIED_MATERIAL_BY_EAN,
    VERIFIED_PICKUP_REQUIREMENT_BY_STATION as VERIFIED_PICKUP_REQUIREMENT_BY_STATION,
    VERIFIED_REQUIREMENT_BY_CUSTOMER as VERIFIED_REQUIREMENT_BY_CUSTOMER,
    VERIFIED_REQUIREMENT_BY_SHIP_TO_CODE as VERIFIED_REQUIREMENT_BY_SHIP_TO_CODE,
)
from .parsing_text import normalize_text as normalize_text
from .parsing_fields import (
    _after_colon as _after_colon,
    _find_label_row as _find_label_row,
    _same_line_values as _same_line_values,
    _strip_label as _strip_label,
    enrich_fields as enrich_fields,
    estimate_field_confidences as estimate_field_confidences,
    extract_field as extract_field,
    extract_joined_field as extract_joined_field,
    parse_fields as parse_fields,
    repair_contextual_fields as repair_contextual_fields,
    standardize_fixed_phrases as standardize_fixed_phrases,
)
from .parsing_products import (
    _cluster_center_y as _cluster_center_y,
    _estimate_product_skew as _estimate_product_skew,
    _normalize_product_value as _normalize_product_value,
    _product_cell_confidence as _product_cell_confidence,
    _product_observation_assignments as _product_observation_assignments,
    _same_decimal as _same_decimal,
    _valid_ean13 as _valid_ean13,
    _product_values_by_columns as _product_values_by_columns,
    parse_product_table as parse_product_table,
    product_table_text as product_table_text,
)
from .parsing_dates import (
    compare_dates as compare_dates,
    compare_partial_date_components as compare_partial_date_components,
    estimate_date_confidence as estimate_date_confidence,
    extract_date_components as extract_date_components,
    find_receipt_date as find_receipt_date,
    format_partial_date as format_partial_date,
    parse_date as parse_date,
    parse_receipt_date as parse_receipt_date,
)
from .parsing_seals import (
    _circular_prefix_company_match as _circular_prefix_company_match,
    _company_core as _company_core,
    _company_name as _company_name,
    _has_conflicting_complete_company as _has_conflicting_complete_company,
    _has_required_suffix as _has_required_suffix,
    _matching_fragmented_local_samsung_service_stamp as _matching_fragmented_local_samsung_service_stamp,
    _matching_samsung_service_stamp as _matching_samsung_service_stamp,
    _matching_station_stamp as _matching_station_stamp,
    _partial_similarity as _partial_similarity,
    compare_seal_text as compare_seal_text,
)

