from typing import Any

from app.modules.posts.agents.asset_intelligence import AssetPolicy
from app.modules.posts.agents.design_spec import DesignSpec
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.semantic_contract import PostSemanticContract
from app.modules.posts.orchestration.supervisor import (
    SupervisorStageContext,
    SupervisorStageResult,
)
from app.modules.posts.tools.generation import GenerationPlanner, GenerationPlannerInput


class GenerationPlanningStageHandler:
    """Chooses the smallest generation scope and writes only generation_plan."""

    async def execute(self, context: SupervisorStageContext) -> SupervisorStageResult:
        output = GenerationPlanner().build(_planner_payload(context.workflow_state))
        return SupervisorStageResult(
            outputs={PostWorkflowSection.GENERATION_PLAN: output.model_dump(mode="json")}
        )


def _planner_payload(workflow_state: dict[str, Any]) -> GenerationPlannerInput:
    contract_value = workflow_state.get(PostWorkflowSection.SEMANTIC_CONTRACT.value)
    assets_value = workflow_state.get(PostWorkflowSection.ASSETS.value)
    if not isinstance(contract_value, dict):
        raise ValueError("semantic_contract must be an object")
    if not isinstance(assets_value, list):
        raise ValueError("assets must be an array")
    contract = PostSemanticContract.from_dict(contract_value)
    return GenerationPlannerInput(
        assets=[AssetPolicy.model_validate(asset) for asset in assets_value],
        design_spec=DesignSpec.model_validate(
            workflow_state.get(PostWorkflowSection.DESIGN_SPEC.value)
        ),
        semantic_contract=contract.to_dict(),
    )


__all__ = ["GenerationPlanningStageHandler"]
