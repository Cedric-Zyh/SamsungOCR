"""Date crop lifecycle boundary."""
from .ocr import regions as date_crop_regions
from . import contracts as date_crop_state
from . import regions as date_crop_workflow
from . import pipeline as date_crops
from .._boundary import install_boundary

install_boundary(__name__, (
    date_crops, date_crop_state, date_crop_workflow, date_crop_regions,
))
