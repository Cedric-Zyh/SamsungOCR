"""Date workflow boundary for the compact, clean-input OCR flow."""
from .preprocess import inputs as date_crop_preparation
from .audit import artifacts as date_crop_region_output
from .ocr import regions as date_crop_regions
from . import contracts as date_crop_state
from . import regions as date_crop_workflow
from . import pipeline as date_crops
from .._boundary import install_boundary

recognize_receipt_date = date_crops._recognize_receipt_date

install_boundary(__name__, (
    date_crops, date_crop_workflow, date_crop_state, date_crop_preparation,
    date_crop_regions, date_crop_region_output,
), extra=("recognize_receipt_date",))
