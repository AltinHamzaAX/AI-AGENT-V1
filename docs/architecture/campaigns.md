# Campaigns module

Campaigns is a separate bounded module under `app/modules/campaigns`. Its future
domain model, orchestration, agents, tools, and persistence contracts remain
isolated from Posts.

A future Campaign workflow may produce a `CampaignCreativeBrief` and submit that
brief through the public Posts service contract:

```text
Campaign -> CampaignCreativeBrief -> PostGenerationService -> Posts workflow
```

Campaigns must never import internal Posts agents or tools. This preserves module
independence and prevents circular dependencies while retaining a single
deployable backend.
