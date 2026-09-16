"""Evidence shared by the stages of one image, never by separate jobs."""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from .document_types import classify_document
from .document_layout import _find_signature_requirement_row
from .execution import recognize_page, record_page_reuse
from .image_processing import decode_qr
from .ocr_backends import recognize_text, observations_text
from .recognition_scope import provider_allowed


@dataclass(frozen=True)
class StageRequest:
    route: dict
    fields: dict = field(default_factory=dict)
    artifact_dir: Path | str | None = None
    artifact_url_prefix: str = ""
    seal_mode: str = "local"
    seal_future: object = None
    acceptance: dict = field(default_factory=dict)


class DocumentContext:
    def __init__(self, source, *, filename=None):
        self.source = Path(source).resolve()
        self.filename = filename or self.source.name
        self._pages = {}
        self._qr = None
        self.document_type = {
            "type": "unclassified",
            "label": "未执行文档类型识别",
            "reliable": False,
            "confidence": 0,
            "reasons": ["仅执行远程印章识别，未验证文档类型"],
        }
        self.has_footer = False
        self.primary_rows = []
        self.primary_backend = None

    def page(self, backend):
        if not provider_allowed(backend):
            raise ValueError(f"当前阶段未授权使用识别方式：{backend}")
        if backend in self._pages:
            record_page_reuse()
            return deepcopy(self._pages[backend])
        rows = recognize_page(self.source, backend, recognize_text)
        self._pages[backend] = deepcopy(rows)
        if not self.document_type["reliable"]:
            classification = classify_document(rows)
            # Empty or incomplete OCR cannot lock out a later authorized
            # provider. Keep the strongest routing evidence seen so far;
            # a reliable document type remains fixed for this run.
            if classification["confidence"] > self.document_type["confidence"]:
                self.document_type = classification
                self.primary_rows = deepcopy(rows)
                self.primary_backend = backend
                self.has_footer = _find_signature_requirement_row(rows) is not None
        return rows

    def cached_page(self, backend):
        if not provider_allowed(backend):
            return []
        return deepcopy(self._pages.get(backend, []))

    def qr(self):
        if self._qr is None:
            self._qr = decode_qr(self.source)
        return self._qr

    @property
    def routing_reasons(self):
        kind = self.document_type["type"]
        if kind == "receipt":
            return (
                [] if self.has_footer else ["回单首页未包含签收页脚，等待商品续页关联"]
            )
        if kind == "product_continuation":
            return [
                (
                    "商品明细续页含签收页脚，需与对应回单首页合并复核"
                    if self.has_footer
                    else "商品明细续页需与对应回单首页合并复核"
                )
            ]
        if kind == "warehouse_authorization":
            return ["仓库货物接收委托书不适用回单日期/印章模板"]
        if kind == "unclassified":
            return list(self.document_type["reasons"])
        return ["未知文档版式，禁止套用回单日期/印章模板"]

    @property
    def allows_remote_seal(self):
        kind = self.document_type["type"]
        return kind == "unclassified" or (
            kind in {"receipt", "product_continuation"} and self.has_footer
        )

    def evidence(self):
        return {
            "filename": self.filename,
            "document_type": deepcopy(self.document_type),
            "raw_text": observations_text(self.primary_rows),
            "qr_text": self._qr or "",
            "ocr_observations": [row.to_dict() for row in self.primary_rows],
        }
