"""Move legacy regression mocks from the former monolith to their consumers.

Assertions and fixture evidence are unchanged; new pipeline tests patch the
specific stage under test directly. This helper is only for old shared mocks.
"""

from importlib import import_module

DEPENDENCIES = {
    "_recognize_date_line_vision_consensus": ["date_crops", "date_evidence"],
    "_recognize_date_slot_with_vision": ["date_crops", "date_evidence"],
    "_recover_signature_requirement": ["field_rules", "stage_fields"],
    "_save_date_line_crop": ["date_crops", "date_evidence"],
    "backend_label": [
        "date_crops",
        "pipeline",
        "seal_crop_regular",
        "seal_crop_mobile",
        "seal_crop_audit",
        "stage_fields",
        "stage_seal",
    ],
    "backend_route": ["pipeline", "recognition_config"],
    "backend_route_labels": [],
    "decode_qr": ["document_context"],
    "detect_seal_regions": ["stage_seal"],
    "extract_region_text": ["seal_crop_regular"],
    "parse_fields": ["stage_fields"],
    "parse_product_table": ["product_rules", "stage_products"],
    "recognize_text": [
        "date_crops", "document_context", "product_rules",
        "seal_crop_regular", "seal_crop_mobile", "seal_crop_audit_images",
        "seal_crop_audit",
    ],
    "resolve_backend": ["pipeline"],
    "save_color_isolated_seal": ["seal_crop_regular"],
    "save_isolated_seal": ["seal_crop_regular"],
    "save_receipt_date_crop": ["date_crops"],
    "save_region_crop": ["seal_crop_regular"],
    "save_round_seal_type_band": ["seal_crop_audit_images"],
    "save_unwrapped_seal": ["seal_crop_regular", "seal_crop_audit_images"],
    "seal_region_is_rectangular": ["seal_crop_regular"],
}


def patch_dependency(monkeypatch, name, value):
    for module in DEPENDENCIES[name]:
        monkeypatch.setattr(import_module("receipt_ocr." + module), name, value)
