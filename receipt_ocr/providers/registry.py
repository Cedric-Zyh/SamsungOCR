"""Static provider capabilities, identifiers and routes.

Definitions describe what a provider supports. Runtime availability, credentials
and request authorization are separate concerns and never inferred from these
definitions. Importing this module performs no I/O and loads no OCR runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    label: str
    stages: frozenset[str]
    kind: str
    model_variant: str | None = None
    models: tuple[str, str] | None = None
    lightweight: bool = False
    page_fallback: str | None = None
    model_name: str = ""
    unavailable_reason: str = ""
    safety_policy: str = ""
    usage_note: str = ""
    recommended: bool = False

    def supports(self, stage: str) -> bool:
        return stage in self.stages

    def route(self) -> dict[str, str]:
        """Return a fresh legacy page/date/seal route for local providers."""
        if self.kind == "local_text":
            return dict.fromkeys(("page", "date", "seal"), self.id)
        if self.kind == "local_seal" and self.page_fallback:
            return {"page": self.page_fallback, "date": self.page_fallback, "seal": self.id}
        raise ValueError(f"识别方式不支持本地 OCR 路由：{self.id}")


class ProviderRegistry:
    """Immutable catalog of the recognition providers supported by the app."""

    def __init__(self, definitions: Iterable[ProviderDefinition]):
        definitions = tuple(definitions)
        by_id = {item.id: item for item in definitions}
        if len(by_id) != len(definitions):
            raise ValueError("重复的识别方式标识")
        for item in definitions:
            if item.page_fallback and (
                item.page_fallback not in by_id
                or by_id[item.page_fallback].kind != "local_text"
            ):
                raise ValueError(f"无效的整页 OCR 路由：{item.id}")
        self._definitions = MappingProxyType(by_id)

    def __iter__(self):
        return iter(self._definitions.values())

    def normalize(self, name: str | None) -> str:
        return (name or "").strip().lower()

    def get(self, name: str | None) -> ProviderDefinition | None:
        return self._definitions.get(self.normalize(name))

    def require(self, name: str) -> ProviderDefinition:
        definition = self.get(name)
        if definition is None:
            raise ValueError(f"未知识别方式：{name}")
        return definition

    def of_kind(self, kind: str) -> tuple[ProviderDefinition, ...]:
        return tuple(item for item in self if item.kind == kind)


PROVIDERS = ProviderRegistry(
    (
        ProviderDefinition(
            id="paddle_v6", label="PaddleOCR PP-OCRv6 Small",
            stages=frozenset({"fields", "products", "handwriting", "date", "seal"}),
            kind="local_text", model_variant="v6",
            models=("PP-OCRv6_small_det", "PP-OCRv6_small_rec"), lightweight=True,
            model_name="PP-OCRv6 small",
            unavailable_reason="需 paddleocr>=3.7.0（PP-OCRv6 模型）",
            safety_policy="单一 Paddle 模型：日期同日可匹配，印章候选进入人工复核",
            usage_note="当前默认本地模型；整页、日期和印章使用同一 v6 路线",
            recommended=True,
        ),
        ProviderDefinition(
            id="paddle_seal", label="PaddleOCR 印章专用检测模型",
            stages=frozenset({"seal"}), kind="local_seal", page_fallback="paddle_v6",
            models=("PP-OCRv4_mobile_seal_det", "PP-OCRv6_small_rec"),
            model_name="PP-OCRv4_mobile_seal_det + PP-OCRv6_small_rec",
            unavailable_reason="需 paddleocr>=3.7.0（印章检测模型）",
            safety_policy="仅读取印章区域三张处理图，不参与页面、日期或商品识别",
            usage_note="印章专用检测；首次使用需下载 PP-OCRv4_mobile_seal_det 权重",
        ),
        ProviderDefinition(
            id="qingtong", label="清瞳印章 API", stages=frozenset({"seal"}), kind="remote_seal",
        ),
        ProviderDefinition(
            id="danzhengtong", label="单证通",
            stages=frozenset({"fields", "handwriting", "date", "seal"}),
            kind="remote_document",
        ),
    ),
)
