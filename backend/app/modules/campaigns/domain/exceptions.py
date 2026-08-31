class CampaignNotFoundError(LookupError):
    pass


class CampaignSourceNotFoundError(LookupError):
    pass


__all__ = ["CampaignNotFoundError", "CampaignSourceNotFoundError"]
