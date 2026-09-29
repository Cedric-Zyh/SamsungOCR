"""Date decision boundary for consensus and reliability selection."""
from .postprocess import confidence as date_confidence_rules
from .postprocess import state as date_decision
from .postprocess import decision as date_selection
from .._boundary import install_boundary

install_boundary(__name__, (date_decision, date_selection, date_confidence_rules))
