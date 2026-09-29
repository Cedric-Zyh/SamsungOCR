"""Normalize OCR-owned date text/components without a required-date hint."""
from dataclasses import replace
from receipt_ocr.domain.parsing import extract_date_components
from ..contracts import DateComponents, DateOcrResult
from .normalize import normalize_date_only_text


def structure_result(result: DateOcrResult) -> DateOcrResult:
    reads = []
    for read in result.reads:
        text = normalize_date_only_text(read.observation.text)
        components = extract_date_components([replace(read.observation, text=text)])
        reads.append(replace(read, text=text, components=DateComponents(**components)))
    return replace(result, reads=tuple(reads))


def date_rows(result: DateOcrResult):
    return [replace(read.observation, text=read.text) for read in result.reads if read.text]
