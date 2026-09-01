class CampaignNotFoundError(LookupError):
    pass


class CampaignSourceNotFoundError(LookupError):
    pass


class InvalidCampaignTransitionError(RuntimeError):
    pass


__all__ = [
    "CampaignNotFoundError",
    "CampaignSourceNotFoundError",
    "InvalidCampaignTransitionError",
]
