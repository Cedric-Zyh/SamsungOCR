"""Public date recognition API used by the application layer."""

from .pipeline import _recognize_receipt_date as recognize_receipt_date

__all__ = ["recognize_receipt_date"]
