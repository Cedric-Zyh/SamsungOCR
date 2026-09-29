"""Seal decision and text-rule boundary."""
from .postprocess import decision as seal_decision
from .postprocess import rules as seal_rules
from .._boundary import install_boundary

install_boundary(__name__, (seal_decision, seal_rules))
