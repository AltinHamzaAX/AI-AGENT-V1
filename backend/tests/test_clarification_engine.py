import pytest
from pydantic import ValidationError

from app.modules.posts.agents.client_understanding import (
    ClientUnderstandingBrief,
    UnderstandingField,
)
from app.modules.posts.domain.clarification import (
    ClarificationEngine,
    ClarificationPlan,
    MissingInformationClass,
)
from app.modules.posts.domain.enums import PostWorkflowSection
from app.modules.posts.domain.state import empty_workflow_state
from app.modules.posts.domain.supervisor import (
    PostSupervisor,
    SupervisorAction,
    SupervisorStage,
)


def test_engine_asks_only_for_critical_missing_information() -> None:
    brief = ClientUnderstandingBrief(
        language="shqip",
        missing_fields=[
            UnderstandingField.BUSINESS,
            UnderstandingField.PRODUCT_SERVICE,
            UnderstandingField.GOAL,
            UnderstandingField.AUDIENCE,
            UnderstandingField.PLATFORM,
            UnderstandingField.OFFER,
        ],
    )

    plan = ClarificationEngine().evaluate(brief)
    classifications = {item.field: item.classification for item in plan.items}

    assert plan.requires_user_input is True
    assert [question.field for question in plan.questions] == [
        UnderstandingField.PRODUCT_SERVICE,
        UnderstandingField.GOAL,
    ]
    assert classifications[UnderstandingField.BUSINESS] is MissingInformationClass.OPTIONAL
    assert classifications[UnderstandingField.PRODUCT_SERVICE] is MissingInformationClass.CRITICAL
    assert classifications[UnderstandingField.GOAL] is MissingInformationClass.CRITICAL
    assert classifications[UnderstandingField.AUDIENCE] is MissingInformationClass.RESEARCHABLE
    assert classifications[UnderstandingField.PLATFORM] is MissingInformationClass.INFERABLE
    assert classifications[UnderstandingField.OFFER] is MissingInformationClass.OPTIONAL
    assert all("?" in question.question for question in plan.questions)


def test_named_business_makes_missing_product_inferable_without_user_friction() -> None:
    brief = ClientUnderstandingBrief(
        business="kafiteri",
        goal="më shumë vizita",
        language="shqip",
        missing_fields=[
            UnderstandingField.PRODUCT_SERVICE,
            UnderstandingField.AUDIENCE,
            UnderstandingField.MARKET,
            UnderstandingField.OFFER,
        ],
    )

    plan = ClarificationEngine().evaluate(brief)

    assert plan.requires_user_input is False
    assert plan.questions == []
    assert plan.items[0].classification is MissingInformationClass.INFERABLE
    assert plan.items[0].resolution == "use_business_as_subject"


def test_plan_rejects_a_critical_item_without_a_question() -> None:
    with pytest.raises(ValidationError, match="critical field"):
        ClarificationPlan.model_validate(
            {
                "requires_user_input": True,
                "items": [
                    {
                        "field": "goal",
                        "classification": "CRITICAL",
                        "resolution": "ask_user",
                        "reason": "Goal is required.",
                    }
                ],
                "questions": [],
            }
        )


def test_supervisor_blocks_only_when_critical_clarification_is_pending() -> None:
    state = empty_workflow_state()
    state[PostWorkflowSection.BRIEF.value] = {
        "clarification": ClarificationEngine()
        .evaluate(
            ClientUnderstandingBrief(
                language="English",
                missing_fields=[UnderstandingField.GOAL],
            )
        )
        .model_dump(mode="json")
    }
    supervisor = PostSupervisor()
    state = supervisor.mark_stage_completed(state, SupervisorStage.CLIENT_UNDERSTANDING)

    decision = supervisor.decide(state)

    assert decision.action is SupervisorAction.STOP
    assert decision.terminal is False
    assert decision.next_stage is SupervisorStage.CLIENT_UNDERSTANDING
    assert decision.required_inputs == ("clarification:goal",)
    assert decision.reason == "critical client information requires clarification"


def test_supervisor_continues_when_only_non_blocking_information_is_missing() -> None:
    state = empty_workflow_state()
    state[PostWorkflowSection.BRIEF.value] = {
        "business": "LUMMA",
        "goal": "more visits",
        "clarification": ClarificationEngine()
        .evaluate(
            ClientUnderstandingBrief(
                business="LUMMA",
                goal="more visits",
                missing_fields=[
                    UnderstandingField.AUDIENCE,
                    UnderstandingField.OFFER,
                ],
            )
        )
        .model_dump(mode="json"),
    }
    supervisor = PostSupervisor()
    state = supervisor.mark_stage_completed(state, SupervisorStage.CLIENT_UNDERSTANDING)

    decision = supervisor.decide(state)

    assert decision.action is SupervisorAction.CONTINUE
    assert decision.next_stage is SupervisorStage.SEMANTIC_CONTRACT
