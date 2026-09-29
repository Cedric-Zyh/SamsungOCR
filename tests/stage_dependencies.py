"""Move legacy regression mocks from the former monolith to their consumers.

Assertions and fixture evidence are unchanged; new pipeline tests patch the
specific stage under test directly. This helper is only for old shared mocks.
"""

from importlib import import_module

DEPENDENCIES = {
    "_recover_signature_requirement": ["domain.fields.rules", "stages.fields"],
    "_save_date_line_crop": ["recognition.date._internal.date_crops", "recognition.date.evidence"],
    "backend_label": [
        "recognition.date._internal.date_crops",
        "application.pipeline",
        "recognition.seal.postprocess.regions",
        "recognition.seal.ocr.secondary",
        "stages.fields",
        "stages.seal",
    ],
    "backend_route": ["application.pipeline", "application.plans"],
    "backend_route_labels": [],
    "decode_qr": ["application.context"],
    "detect_seal_regions": ["stages.seal"],
    "extract_region_text": ["recognition.seal.preprocess.regions"],
    "parse_fields": ["stages.fields"],
    "parse_product_table": ["domain.products.rules", "stages.products"],
    "recognize_text": [
        "recognition.date._internal.date_crops", "application.context", "domain.products.rules",
        "recognition.seal.ocr.interface", "recognition.seal.ocr.region",
    ],
    "resolve_backend": ["application.pipeline"],
    "save_color_isolated_seal": ["recognition.seal.preprocess.regions", "recognition.seal.preprocess.common"],
    "save_isolated_seal": ["recognition.seal.preprocess.regions"],
    "save_receipt_date_crop": ["recognition.date._internal.date_crops"],
    "save_region_crop": ["recognition.seal.preprocess.regions", "recognition.seal.preprocess.common"],
    "save_round_seal_type_band": ["recognition.seal.preprocess.regions", "recognition.seal.preprocess.secondary_images"],
    "save_unwrapped_seal": ["recognition.seal.preprocess.regions", "recognition.seal.preprocess.secondary_images"],
    "seal_region_is_rectangular": ["recognition.seal.preprocess.shapes"],
}

ATTRIBUTE_ALIASES = {
    ("recognize_text", "recognition.seal.preprocess.regions"): "read_text",
    ("recognize_text", "recognition.seal.ocr.interface"): "read_text",
    ("recognize_text", "recognition.seal.ocr.region"): "read_text",
}


def patch_dependency(monkeypatch, name, value):
    for module in DEPENDENCIES[name]:
        target = import_module("receipt_ocr." + module)
        attribute = ATTRIBUTE_ALIASES.get((name, module), name)
        monkeypatch.setattr(target, attribute, value)
