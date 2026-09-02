class CampaignNotFoundError(LookupError):
    pass


class CampaignSourceNotFoundError(LookupError):
    pass


class InvalidCampaignTransitionError(RuntimeError):
    pass


class CampaignPlanValidationError(ValueError):
    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("Campaign Plan validation failed: " + ", ".join(issues))


__all__ = [
    "CampaignNotFoundError",
    "CampaignPlanValidationError",
    "CampaignSourceNotFoundError",
    "InvalidCampaignTransitionError",
]
