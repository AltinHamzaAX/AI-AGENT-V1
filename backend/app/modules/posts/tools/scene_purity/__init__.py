"""Gate that keeps a contaminated generated scene out of the composition."""

from .inspector import ScenePurityInspector
from .policy import (
    CLEAN_DETAIL,
    CONFIDENCE_THRESHOLD,
    ScenePurityAssessment,
    decide_scene_purity,
)
from .schemas import (
    SCENE_PURITY_SCHEMA_VERSION,
    ContaminationKind,
    SceneObservation,
    ScenePurityCheck,
    ScenePurityFinding,
    ScenePurityInput,
    ScenePurityReport,
    ScenePurityVerdict,
    SceneReadout,
)

__all__ = [
    "CLEAN_DETAIL",
    "CONFIDENCE_THRESHOLD",
    "SCENE_PURITY_SCHEMA_VERSION",
    "ContaminationKind",
    "SceneObservation",
    "SceneReadout",
    "ScenePurityAssessment",
    "ScenePurityCheck",
    "ScenePurityFinding",
    "ScenePurityInput",
    "ScenePurityInspector",
    "ScenePurityReport",
    "ScenePurityVerdict",
    "decide_scene_purity",
]
