"""Date confidence and review-gating boundary."""
from .postprocess import confidence as date_confidence_rules
from .._boundary import install_boundary

install_boundary(__name__, (date_confidence_rules,))
