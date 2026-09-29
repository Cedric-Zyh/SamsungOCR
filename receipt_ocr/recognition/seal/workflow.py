"""Public seal recognition workflow boundary."""
from . import pipeline
from .._boundary import install_boundary

recognize_local_seals = pipeline._recognize_local_seals
install_boundary(__name__, (pipeline,), extra=("recognize_local_seals",))
