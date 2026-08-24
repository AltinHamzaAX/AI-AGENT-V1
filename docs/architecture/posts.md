# Posts module

Posts is a bounded business module under `app/modules/posts`. It owns its future
services, orchestration, agents, tools, domain types, schemas, and repository
contracts.

The planned workflow may eventually coordinate client understanding, brand and
product analysis, audience research, strategy, creative direction, copywriting,
art direction, and critique. Those capabilities are represented only as package
boundaries today; no workflow or agent behavior has been implemented.

Other modules must enter Posts through a public module-level application service,
such as `PostGenerationService`. They must not import Posts agents, tools, or
orchestration internals.
