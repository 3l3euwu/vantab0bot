"""Vantab's autonomous moderation engine."""

from .engine import AutoModEngine, AutoModResult
from .rules import RULES, Detection, MessageContext, RuleSpec, ViolationTracker, evaluate

__all__ = [
    "AutoModEngine",
    "AutoModResult",
    "RULES",
    "Detection",
    "MessageContext",
    "RuleSpec",
    "ViolationTracker",
    "evaluate",
]
