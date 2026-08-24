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

## Semantic contract

`PostSemanticContract` is the write-once semantic source of truth for one
generation. It records company, brand, product, primary entity, goal, audience,
market, location, offer, CTA intent, platform, language, required facts,
forbidden claims, required assets, and constraints. Canonical JSON is hashed with
SHA-256, and the stored fingerprint is verified whenever the contract is loaded.

The generic workflow-state operation cannot mutate `semantic_contract`. Repeating
the dedicated create operation with the same fingerprint is idempotent; trying to
replace it with a different contract returns `SEMANTIC_CONTRACT_HARD_FAIL`.
Downstream stages can submit only the semantic assertions they make. Comparison
normalizes Unicode, case, and whitespace while preserving the exact persisted
truth. Product/offer/fact drift, a forbidden claim, a missing required asset, or
a mismatched fingerprint is a hard failure. The future Supervisor may route on
this structured decision but does not own or rewrite the contract.

## Agent framework and tool registry

The internal agent framework is deny-by-default. `AgentDefinition` declares a
stable name, role, Pydantic input/output schemas, allowed tools, timeout, and
retry policy. `ToolDefinition` declares its category, schemas, allowed agents,
timeout/retry policy, and security capabilities. Registration names are unique.

Agents do not receive the registry or raw tool handlers. `AgentRuntime` gives a
registered agent a gateway bound to its internal identity token and invocation
context. A call proceeds only when both allowlists agree and mandatory capability
restrictions pass. In particular, `creative_director` cannot invoke final
approval, database mutation, or asset replacement capabilities. A denial never
executes or retries the handler and is emitted as `posts.tool.denied` with agent,
tool, reason, and correlation identifiers—but never the payload.

Agent and tool inputs and outputs are schema-validated. Per-component timeouts
and bounded retry policies cover transient errors and timeouts; authorization
failures are never retryable. This ticket provides the execution contract only:
specialist behavior, provider adapters, Supervisor routing, and workflow jobs are
implemented by later tickets.

Other modules must enter Posts through a public module-level application service,
such as `PostGenerationService`. They must not import Posts agents, tools, or
orchestration internals.
