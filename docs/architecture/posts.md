# Posts module

Posts is a bounded business module under `app/modules/posts`. It owns its
services, orchestration, agents, tools, domain types, schemas, and repository
contracts.

The planned workflow may eventually coordinate client understanding, brand and
product analysis, audience research, strategy, creative direction, copywriting,
art direction, and critique. Those capabilities remain package boundaries today;
no workflow or agent behavior has been implemented.

The implemented persistence model is intentionally smaller than the future
workflow:

```text
Post
  -> PostGeneration attempt 1..N
       -> GenerationArtifact 0..N
```

Generation status is explicit (`pending`, `queued`, `running`, `reviewing`,
`revision`, `completed`, `failed`, or `cancelled`). Creating an attempt does not
run AI work in the HTTP request; worker dispatch and recoverable workflow state
belong to later tickets. A nullable `campaign_id` keeps standalone and future
Campaign-created posts on the same Posts model.

Other modules must enter Posts through a public module-level application service,
such as `PostGenerationService`. They must not import Posts agents, tools, or
orchestration internals.
