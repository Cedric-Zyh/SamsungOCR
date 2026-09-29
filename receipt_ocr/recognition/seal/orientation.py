"""Public orientation boundary for shape-aware preprocessing."""
from .preprocess import orientation as _impl
from .._boundary import install_boundary

install_boundary(__name__, (_impl,))
