from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.posts.domain.enums import UnderstandingField


class ClarificationBrief(Protocol):
    business: str | None
    language: str | None
    missing_fields: list[UnderstandingField]


class MissingInformationClass(StrEnum):
    CRITICAL = "CRITICAL"
    OPTIONAL = "OPTIONAL"
    INFERABLE = "INFERABLE"
    RESEARCHABLE = "RESEARCHABLE"


class MissingInformationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: UnderstandingField
    classification: MissingInformationClass
    resolution: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: UnderstandingField
    question: str = Field(min_length=1, max_length=500)


class ClarificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_user_input: bool
    items: list[MissingInformationItem]
    questions: list[ClarificationQuestion] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_plan(self) -> "ClarificationPlan":
        item_fields = [item.field for item in self.items]
        if len(item_fields) != len(set(item_fields)):
            raise ValueError("missing-information fields must be unique")
        question_fields = [question.field for question in self.questions]
        if len(question_fields) != len(set(question_fields)):
            raise ValueError("clarification question fields must be unique")
        critical = {
            item.field
            for item in self.items
            if item.classification is MissingInformationClass.CRITICAL
        }
        if set(question_fields) != critical:
            raise ValueError("every critical field must have exactly one question")
        if self.requires_user_input != bool(critical):
            raise ValueError("requires_user_input must match critical missing information")
        return self


class ClarificationEngine:
    """Classify missing client facts while minimizing user-facing questions."""

    def evaluate(self, brief: ClarificationBrief) -> ClarificationPlan:
        items = [self._classify(field, brief) for field in brief.missing_fields]
        questions = [
            ClarificationQuestion(
                field=item.field,
                question=_question(item.field, language=brief.language),
            )
            for item in items
            if item.classification is MissingInformationClass.CRITICAL
        ]
        return ClarificationPlan(
            requires_user_input=bool(questions),
            items=items,
            questions=questions,
        )

    def _classify(
        self,
        field: UnderstandingField,
        brief: ClarificationBrief,
    ) -> MissingInformationItem:
        if field is UnderstandingField.GOAL:
            return _item(
                field,
                MissingInformationClass.CRITICAL,
                "ask_user",
                "The business outcome controls every downstream strategy decision.",
            )
        if field is UnderstandingField.PRODUCT_SERVICE:
            if brief.business:
                return _item(
                    field,
                    MissingInformationClass.INFERABLE,
                    "use_business_as_subject",
                    "The named business can safely remain the promoted subject.",
                )
            return _item(
                field,
                MissingInformationClass.CRITICAL,
                "ask_user",
                "A post cannot be planned without knowing what it promotes.",
            )
        if field in {UnderstandingField.AUDIENCE, UnderstandingField.MARKET}:
            return _item(
                field,
                MissingInformationClass.RESEARCHABLE,
                "route_to_research",
                "This context can be investigated without interrupting the user.",
            )
        if field in {UnderstandingField.PLATFORM, UnderstandingField.LANGUAGE}:
            return _item(
                field,
                MissingInformationClass.INFERABLE,
                "infer_from_context",
                "Conversation or delivery context can supply a safe working value.",
            )
        return _item(
            field,
            MissingInformationClass.OPTIONAL,
            "continue_without_value",
            "The workflow can continue and omit this non-essential detail.",
        )


def _item(
    field: UnderstandingField,
    classification: MissingInformationClass,
    resolution: str,
    reason: str,
) -> MissingInformationItem:
    return MissingInformationItem(
        field=field,
        classification=classification,
        resolution=resolution,
        reason=reason,
    )


def _question(field: UnderstandingField, *, language: str | None) -> str:
    albanian = (language or "").strip().casefold() in {"albanian", "shqip", "shqipe", "sq"}
    if field is UnderstandingField.GOAL:
        return (
            "Cili është rezultati kryesor që dëshironi nga ky post?"
            if albanian
            else "What is the main result you want from this post?"
        )
    if field is UnderstandingField.PRODUCT_SERVICE:
        return (
            "Cilin produkt, shërbim ose biznes duhet të promovojë postimi?"
            if albanian
            else "Which product, service, or business should the post promote?"
        )
    raise ValueError(f"No clarification question exists for field '{field.value}'")


__all__ = [
    "ClarificationEngine",
    "ClarificationPlan",
    "ClarificationQuestion",
    "MissingInformationClass",
    "MissingInformationItem",
]
