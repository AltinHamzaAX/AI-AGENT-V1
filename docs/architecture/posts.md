# Posts module

Posts is a bounded business module under `app/modules/posts`. It owns its
services, orchestration, agents, tools, domain types, schemas, and repository
contracts.

The planned workflow may eventually coordinate client understanding, brand and
product analysis, audience research, strategy, creative direction, copywriting,
art direction, and critique. Those agent behaviors remain package boundaries;
the durable state contract they will read and write is implemented.

The implemented persistence model is intentionally smaller than the future
workflow:

```text
Post
  -> PostGeneration attempt 1..N
       -> GenerationArtifact 0..N
       -> PostGenerationState current version 1..N
            -> PostGenerationStateVersion snapshot 1..N
```

Generation status is explicit (`pending`, `queued`, `running`, `reviewing`,
`revision`, `completed`, `failed`, or `cancelled`). Creating an attempt does not
run AI work in the HTTP request; worker dispatch belongs to a later ticket. A
nullable `campaign_id` keeps standalone and future Campaign-created posts on the
same Posts model.

The workflow state contains explicit sections for conversation context, brief,
semantic contract, brand, product, assets, audience, research, marketing
strategy, creative concept, copy, art direction, design specification,
generation plan and artifacts, quality, and revision history. A write changes
exactly one section, validates whether it is an object or array, and uses an
`expected_version` compare-and-swap. This makes concurrent stage writes safe and
lets a worker resume from PostgreSQL after restart instead of relying on implicit
LLM memory. Full snapshots are retained for every accepted version.

Other modules must enter Posts through a public module-level application service,
such as `PostGenerationService`. They must not import Posts agents, tools, or
orchestration internals.
