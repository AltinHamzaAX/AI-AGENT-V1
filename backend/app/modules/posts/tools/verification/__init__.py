"""Hard verification gates: the last thing between a render and the client.

These gates do not score. Each one is a yes-or-no question about whether the
post is contractually allowed to ship, and one failure blocks it whatever the
marketing and design reviews thought of it.
"""

from .policy import (
    CLEAN_DETAIL,
    IDENTITY_KINDS,
    MAX_EXPORT_SCALE,
    MIN_TEXT_LENGTH,
    PRODUCT_ROLES,
    VerificationAssessment,
    decide_verification,
)
from .schemas import (
    RENDER_READOUT_WIRE_SCHEMA,
    VERIFICATION_SCHEMA_VERSION,
    GateCheck,
    GateFailure,
    RenderReadout,
    VerificationDecision,
    VerificationGate,
    VerificationInput,
    VerificationReport,
)
from .verifier import WITNESS_MAX_EDGE, HardVerificationGate

__all__ = [
    "CLEAN_DETAIL",
    "IDENTITY_KINDS",
    "MAX_EXPORT_SCALE",
    "MIN_TEXT_LENGTH",
    "PRODUCT_ROLES",
    "RENDER_READOUT_WIRE_SCHEMA",
    "VERIFICATION_SCHEMA_VERSION",
    "WITNESS_MAX_EDGE",
    "GateCheck",
    "GateFailure",
    "HardVerificationGate",
    "RenderReadout",
    "VerificationAssessment",
    "VerificationDecision",
    "VerificationGate",
    "VerificationInput",
    "VerificationReport",
    "decide_verification",
]
