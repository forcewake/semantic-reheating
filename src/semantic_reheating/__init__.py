"""Deterministic public API for semantic recovery decisions."""

from .controller import (
    ControllerError,
    RecoveryInstruction,
    SemanticDetector,
    analyze,
    build_recovery_instruction,
)
from .evidence import EvidenceError, EvidenceRecord, RecoveryOutcome, record_outcome
from .models import Decision, DecisionEnvelope, RunPolicy, TraceEvent

__version__ = "0.1.0"

__all__ = (
    "ControllerError",
    "Decision",
    "DecisionEnvelope",
    "EvidenceError",
    "EvidenceRecord",
    "RecoveryInstruction",
    "RecoveryOutcome",
    "RunPolicy",
    "SemanticDetector",
    "TraceEvent",
    "__version__",
    "analyze",
    "build_recovery_instruction",
    "record_outcome",
)
