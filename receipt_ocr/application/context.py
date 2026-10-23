"""Evidence shared by the stages of one image, never by separate jobs."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from ..domain.documents.types import classify_document
from ..domain.issues import issue
from ..domain.documents.layout import signature_footer_evidence
from ..domain.ocr import TextObservation
from ..runtime.execution import recognize_page, record_page_reuse, measure
from ..imaging.printed_page import printed_page_image
from ..imaging.page import decode_qr
from ..imaging.io import ImageCache, image_cache_scope
from ..providers.catalog import observations_text
from ..providers.text import TextRecognizer, default_text_recognizer
from ..runtime.scope import provider_allowed




class DocumentContext:
    def __init__(self, source, *, filename=None, text_recognizer: TextRecognizer | None = None):
        self.source = Path(source).resolve()
        self.filename = filename or self.source.name
        self._pages = {}
        self._printed_image = None
        self._qr = None
        self.document_type = {
            "type": "unclassified",
            "label": "未执行文档类型识别",
            "reliable": False,
            "confidence": 0,
            "reasons": ["仅执行远程印章识别，未验证文档类型"],
        }
        self.has_footer = False
        self.footer_anchor = None
        self.footer_detection = {}
        self.primary_rows = []
        self.primary_backend = None
        self.text_recognizer = text_recognizer if text_recognizer is not None else default_text_recognizer()
        self.image_cache = ImageCache()

    def image_scope(self):
        """Return the per-run image cache scope used by the stage coordinator."""

        return image_cache_scope(self.image_cache)

    def page(self, backend):
        if not provider_allowed(backend):
            raise ValueError(f"当前阶段未授权使用识别方式：{backend}")
        if backend in self._pages:
            record_page_reuse()
            return deepcopy(self._pages[backend])
        rows = recognize_page(self.source, backend, self._recognize_printed_page)
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
        self.refresh_footer(rows)
        return rows

    def printed_page_image(self):
        """Return a copy of the exact, full-size printed OCR input pixels."""
        if self._printed_image is None:
            with measure("printed_page_preprocess"):
                self._printed_image = printed_page_image(self.source)
        return self._printed_image.copy()

    def _recognize_printed_page(self, source, *, backend):
        # Keep the reusable page key on the original source. The temporary
        # image is lossless, and never becomes the date/seal stage's source.
        with TemporaryDirectory(prefix="receipt-printed-page-") as directory:
            path = Path(directory) / "page-color-clean.png"
            self.printed_page_image().save(path)
            return self.text_recognizer.recognize(path, backend=backend)

    def refresh_footer(self, rows, recovered_row=None):
        """A confirmed document type must not freeze incomplete footer evidence."""
        evidence = signature_footer_evidence(rows, recovered_row)
        if evidence['present'] or not self.has_footer:
            self.has_footer = evidence['present']
            self.footer_anchor = evidence['anchor']
            self.footer_detection = {k: v for k, v in evidence.items() if k != 'anchor'}
            if self.footer_anchor is not None:
                self.footer_detection['anchor'] = self.footer_anchor.to_dict()

    def confirm_requirement_footer(self, recovered):
        row = recovered.get('footer_row')
        if row:
            self.refresh_footer(self.primary_rows, TextObservation(**row))

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
                [] if self.has_footer else ["签收页脚尚未确认，请核对本页或关联续页"]
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
    def routing_issues(self):
        kind = self.document_type["type"]
        code = ("footer_unconfirmed" if kind == "receipt" and not self.has_footer else
                "awaiting_continuation" if kind == "product_continuation" else "document_routing")
        return [issue(code, "document", message) for message in self.routing_reasons]

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
            "signature_footer": deepcopy(self.footer_detection),
        }
