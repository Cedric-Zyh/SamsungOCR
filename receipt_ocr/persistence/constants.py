"""Persistence constants shared by the database adapters."""

REVIEW_STATUSES = {"无需复核", "待复核", "确认通过", "确认不通过"}
DEFAULT_RETENTION_DAYS = 7
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650

DEFAULT_RECOGNITION_SETTINGS = {
    "recognition_config": {
        "fields": ["paddle_v6"],
        "products": [],
        "handwriting": ["paddle_v6"],
        "date": ["paddle_v6"],
        "seal": ["paddle_v6", "qingtong"],
        "seal_orientation": "polygon",
    },
    "acceptance_policy": {
        "seal_match_mode": "any",
        "date_match_mode": "any",
        "signature_match_mode": "none",
        "reject_mode": "any_mismatch",
        "low_confidence_mode": "ignore",
        "seal_pass_standard": "any_exact",
        "date_source": "danzhengtong",
    },
}

