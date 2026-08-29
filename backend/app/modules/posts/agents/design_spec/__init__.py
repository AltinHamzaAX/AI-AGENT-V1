"""Versioned machine-readable design contract boundary."""

from .agent import (
    DESIGN_SPEC_AGENT_NAME,
    DESIGN_SPEC_DEFINITION,
    DesignSpecAgent,
    register_design_spec_agent,
)
from .schemas import (
    DESIGN_SPEC_SCHEMA_VERSION,
    Bounds,
    Canvas,
    ColorToken,
    DesignSpec,
    DesignSpecBody,
    DesignSpecInput,
    GraphicElement,
    Grid,
    RegionSet,
    SafeArea,
    TypographyRole,
)

__all__ = [
    "DESIGN_SPEC_AGENT_NAME", "DESIGN_SPEC_DEFINITION", "DesignSpecAgent",
    "DESIGN_SPEC_SCHEMA_VERSION", "Bounds", "Canvas", "ColorToken", "DesignSpec",
    "DesignSpecBody", "DesignSpecInput", "GraphicElement", "Grid", "RegionSet", "SafeArea",
    "TypographyRole", "register_design_spec_agent",
]
