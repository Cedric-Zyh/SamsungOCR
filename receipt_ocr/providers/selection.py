"""Provider capability validation for a user's recognition plan."""

from collections.abc import Collection

from .registry import PROVIDERS


def validate_stage_provider(
    stage: str, method: str, *, available: Collection[str], api_enabled: bool,
) -> None:
    """Check capability and availability without initializing any provider.

The remote document service is configured independently of the local OCR
catalog. QingTong additionally requires the API-enabled setting supplied by
the caller. Local entries require both capability and runtime availability.
"""
    definition = PROVIDERS.get(method)
    if definition is None:
        raise ValueError(f"识别方式不可用：{method}")
    if definition.kind == "remote_seal":
        if not definition.supports(stage) or not api_enabled:
            raise ValueError("清瞳仅支持印章识别，且需要配置接口密钥")
    elif not definition.supports(stage) or (
        definition.kind != "remote_document" and definition.id not in available
    ):
        raise ValueError(f"识别方式不可用：{method}")
