"""Public reading policy boundary for OCR providers and result channels."""
from . import reading_policy as _reading
from .postprocess import providers as _providers
from .postprocess import channels as _channels
from .._boundary import install_boundary

install_boundary(__name__, (_reading, _providers, _channels))
