"""The only provider boundary for date line OCR.

Display inputs are rejected before a provider is called. Recognition failures
and genuinely empty reads remain distinguishable in the result contract.
"""
from receipt_ocr.providers import catalog, paddle_runtime
from ..contracts import DateRead, DateOcrResult, PreparedDateInput
from ..contracts import DateInputKind


class DateOcrReader:
    def read(self, image: PreparedDateInput, *, provider: str, line: bool = True,
             recognize_text=None, **options) -> DateOcrResult:
        if not isinstance(image, PreparedDateInput):
            raise TypeError("日期 OCR 需要带用途声明的输入图")
        if not image.ocr_input or image.kind == DateInputKind.ORIGINAL:
            raise ValueError("日期原图或展示图不参与识别")
        model = paddle_runtime.variant_of(provider) or provider
        try:
            if line and paddle_runtime.is_lightweight_backend(provider):
                rows = paddle_runtime.recognize_line(image.path, model_variant=model)
            else:
                recognize = recognize_text or catalog.recognize_text
                rows = recognize(image.path, backend=provider, **options)
        except Exception as exc:
            return DateOcrResult(image, provider, model, status="failed", error=str(exc))
        reads = tuple(DateRead(image.id, provider, model, image.role, row)
                      for row in rows if row.text)
        return DateOcrResult(image, provider, model, reads,
                             "completed" if reads else "empty")


def read_audit_line(*_args, **_kwargs):
    """Fail explicitly if a retired cross-model audit is invoked."""
    raise RuntimeError("日期 Mobile/Server 审计分支已停用")
