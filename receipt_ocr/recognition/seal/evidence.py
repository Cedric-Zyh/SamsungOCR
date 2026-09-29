"""Seal evidence boundary for prepared regions and OCR artifacts."""
from .ocr import secondary as seal_secondary
from . import contracts as seal_contracts
from .preprocess import regions as seal_regions
from .postprocess import regions as seal_results
from .._boundary import install_boundary

install_boundary(__name__, (seal_contracts, seal_secondary, seal_regions, seal_results))
