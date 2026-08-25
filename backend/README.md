# Promotiva backend

FastAPI API and background worker for the Promotiva modular monolith. See the root
README and `docs/architecture` for setup and boundary guidance.

## Persistence boundaries

Application and domain code depend on contracts under `app/repositories` or the
owning module. SQLAlchemy implementations live under
`app/infrastructure/database`. Repositories flush changes but never commit them;
the caller owns an explicit Unit of Work transaction. `SQLAlchemyUnitOfWork`
rolls back on exceptions, explicit rollback, and context exit without commit.

Apply all schema and extension changes through Alembic:

```bash
alembic upgrade head
```

## Asset boundary

Asset validation, application behavior, and storage/repository ports live under
`app/shared/assets`. SQLAlchemy and S3-compatible implementations live under
`app/infrastructure`. Uploads are scoped to a verified conversation message,
stored privately, validated by decoded image content, and deduplicated by SHA-256
without exposing MinIO-specific behavior to Posts code.

## Posts persistence

Posts business entities, statuses, schemas, repository ports, and application
services live under `app/modules/posts`. SQLAlchemy models and adapters remain in
infrastructure. `PostGeneration` owns the attempt number and lifecycle status;
`Post` is the stable container shared by standalone and future Campaign callers.
`PostGenerationState` is created atomically with each generation and persists the
complete structured workflow independently from worker memory. Section-scoped
writes use optimistic version checks and retain full snapshots for recovery and
audit history.

The `semantic_contract` section is not writable through the generic state patch.
It is created idempotently through a dedicated service operation, then protected
by a canonical SHA-256 fingerprint. Downstream assertions can be checked without
an LLM call; product/fact drift, forbidden claims, missing required assets, or a
fingerprint mismatch produce a structured `SEMANTIC_CONTRACT_HARD_FAIL`.

Generation requests atomically create a PostgreSQL-backed job and return its
`job_id`. An optional `Idempotency-Key` prevents duplicate generation attempts.
The worker runtime claims jobs with expiring leases, applies timeout and bounded
retry policies, and persists `completed`, `failed`, or `dead` outcomes. Expired
leases are reclaimable after process restart.

## Post Supervisor

The generation worker delegates workflow execution through
`PostSupervisorExecutor`. Its declared stage graph evaluates dependencies,
required state, existing outputs, retry counts, targeted revisions, and quality
termination gates. Every decision and stage completion is persisted in the
versioned `supervisor` workflow section, so a reclaimed job resumes from its
checkpoint. Stage handlers write only their declared sections; provider and
specialist logic remain outside the Supervisor.

## Agent and tool framework

Posts agents execute through `AgentRuntime`; tools are available only through a
gateway bound to the registered agent identity. Authorization requires both the
agent's `allowed_tools` and the tool's `allowed_agents`. Inputs and outputs are
validated with declared Pydantic schemas, execution is bounded by explicit
timeout/retry policies, and lifecycle events carry correlation, post, and
generation identifiers without logging request payloads. Creative Director is
always denied final approval, database mutation, and asset replacement tools,
even if a definition is misconfigured to allow them.

## AI providers

Posts owns typed ports for LLM, vision, image, embedding, research, and storage
capabilities. `app.integrations.provider_factory` selects adapters exclusively
from configuration, keeping Ollama, Hugging Face, Tavily, and S3-specific details
outside agents and workflow state. Every capability has a deterministic mock for
unit and orchestration tests. Provider failures expose only a safe provider name
and status category, never tokens or raw response bodies.

## Execution tracing

Agent runs, tool runs, provider calls, and Supervisor generation steps can emit
durable records to `post_execution_traces`. Each record contains correlation and
generation identifiers, status, SHA-256 input/output references, duration,
retry count, safe error code, and provider/model/token/cost metadata when the
adapter supplies it. Raw prompts, payloads, images, provider responses, and
credentials are never stored in the trace.

The scoped endpoint
`GET /api/posts/{post_id}/generations/{generation_id}/traces` returns the ordered
execution timeline for diagnostics. Run `alembic upgrade head` before using it.

## Client Understanding Agent

The first Posts specialist consumes structured conversation history, the latest
message, attachment metadata, and verified project context. It emits only the
factual `brief` section: business, brand, product/service, goal, audience,
market/location, platform/language, offer, CTA intent, style preferences,
constraints, exact asset references, and explicitly missing fields.

The deterministic Clarification Engine classifies every missing field as
`CRITICAL`, `OPTIONAL`, `INFERABLE`, or `RESEARCHABLE`. Only critical facts
produce user-facing questions (at most three). Its plan is persisted under
`brief.clarification`; the Supervisor pauses before downstream work only when
that plan declares `requires_user_input=true`.

Verified project values override model extraction, and attachment identity is
rebuilt deterministically from the input rather than trusted to the model. Logo,
product, vehicle, and packaging assets are marked for identity preservation.
The output schema rejects research, positioning, strategy, concept, copy, or
design fields. The Supervisor requires `conversation_context` before routing to
this stage.

## Brand & Product Strategist Agent

The Ticket 15 specialist consumes only the immutable semantic contract and
writes exactly the `brand` and `product` workflow sections. Protected company,
brand, product, offer, constraint, asset, and fingerprint values are rebuilt
from the contract rather than trusted to the model.

Every product feature must cite a verified `required_facts` key and is expressed
as `FEATURE -> BENEFIT -> CUSTOMER VALUE`. Brand and product facts are explicitly
classified without dropping any required fact, while every USP candidate keeps
its supporting fact references. Unknown facts, product replacement, forbidden
claims, or downstream strategy/copy fields fail closed under the bounded agent
retry policy. Generated internal analysis is written in concise English for
consistent model quality, while authoritative names, offers, and fact values
remain exactly as supplied. The requested content language is consumed later by
the Copywriter stage.

## Audience Intelligence Agent

The Ticket 17 specialist reads the immutable semantic contract plus validated
brand and product analysis, then writes exactly the `audience` workflow section.
It produces segments, one selected target, needs, desires, pain points,
objections, motivation, purchase intent, trust triggers, usage context, and an
explicit current-state to desired-state customer tension.

Every derived insight carries an allowlisted input `basis` and a confidence
level. Protected audience, market, location, platform, and contract fingerprint
are rebuilt from the semantic contract. Generated insights remain clearly marked
as hypotheses until Ticket 18 External Research validates them. The strict
schema excludes positioning, USP selection, marketing strategy, copy, creative
concepts, and design decisions.

## Asset Intelligence Engine

The Ticket 16 specialist classifies every conversation attachment before
production and writes exactly the `assets` workflow section. Its role vocabulary
is `brand_logo`, `primary_product`, `vehicle`, `packaging`, `environment`,
`background_reference`, `style_reference`, `supporting_asset`, and
`inspiration_only`.

User-declared roles remain authoritative, while an exact user-intent quote may
promote a vehicle or ambiguous supporting attachment to the primary product.
Policy fields are never delegated to the model: required use, identity
preservation, crop/replacement/generation permissions, and dominance bounds are
derived deterministically. Missing required assets fail before a provider call.
The reusable usage gate returns `HARD_FAIL` for a missing or replaced protected
asset, identity drift, forbidden crop or generation, or invalid dominance.

## External Research Service

Ticket 18 runs eight bounded research tools for market, competitors, audience,
social behavior, visual references, trends, platform guidance, and verified
brand/product context. `ExternalResearchService` schedules independent calls
concurrently through the provider-neutral `ResearchProvider` and writes exactly
the `research` workflow section.

Every report is structured, timestamped, confidence-aware, and source-aware:
findings retain their source URL, retrieval time, provider score, and explicit
`external_evidence` authority. Research never mutates the semantic contract or
produces positioning, strategy, copy, concepts, or design decisions. Cache keys
include the category, canonical query, and contract fingerprint. A Redis adapter
supports cross-worker TTL caching, while tests use the deterministic in-memory
adapter. Successful provider work survives cache failures, and a retry can reuse
the reports already cached by other categories.

Ticket 19 adds strict, source-grounded analysis contracts to the market,
competitor, and social tools. Market reports cover category context, market and
customer expectations, observed offers, positioning patterns, and opportunities.
Competitor reports cover messaging, offers, CTA, visual language,
differentiation, and overused patterns. Social reports cover platform creative
patterns, text density, CTA, logo placement, photography, graphic systems, and
composition. Each dimension gets its own focused query. Results below the
relevance threshold are removed, while retained sources receive explicit
authority, locality, freshness, and composite quality scores. The model may cite
only source IDs supplied by the search step and must include an exact excerpt
quote; application code verifies both the quote and its allowed dimension before
mapping it to the report URL. Unknown or fabricated citations fail closed.
Unsupported claims are omitted and exposed through complete, partial, or
insufficient evidence coverage with named missing dimensions. Competitor output
carries the invariant `differentiate_do_not_copy`, and copy/replication
instructions are rejected. These observations remain research evidence, not
strategy, copy, creative direction, or design instructions.

### Provider targeting

Locality, recency, and source authority are bought at query time rather than
recovered by scoring afterwards. Market-facing tools resolve the declared market
or location to a provider-supported country and geo-target every request; the
trend tool trades geo-targeting for the news index with a bounded recency
window; and the platform tool pins the platform's own documentation domains
instead of competing with SEO recaps of it. A market with no supported
equivalent omits the parameter rather than targeting a neighbouring country.
Kosovo is the one deliberate exception: the provider has no Kosovo value, so
Kosovo markets are geo-targeted to Albania for shared-language regional
coverage, and results that are Albania-specific rather than Kosovo-specific are
still demoted by `locality_score`, which scores against the declared market
text. Domain exclusions are an opt-in tuning hook and empty by default, because
low-authority sources are demoted by `authority_score` rather than excluded,
which keeps recall when nothing better exists.

### Source quality

Each source is scored on relevance, authority, locality and freshness, and its
confidence follows that composite rather than a model's opinion. Two properties
keep the top of the scale honest and reachable.

Locality folds diacritics and matches stems, so a page written "Prishtinë" or
"Kosovës" counts as local. Plain substring matching scored those exactly as low
as a page about another continent, penalising sources for being local — the
single biggest reason nothing reached high confidence in an Albanian market.
Generic geography words like "airport" are excluded from the comparison,
because they describe a place without identifying one and requiring them put
the top of the scale out of reach.

Freshness is optional. Most pages carry no publication date, and treating that
absence as "not fresh" charged nearly every source for a measurement never
taken. When the date is unknown, `freshness_score` is `None` and its weight is
redistributed across the signals that were measured, so a source is judged on
what is known about it. The confidence thresholds themselves are unchanged: a
weak or off-market source still scores low.

### Evidence depth

Search snippets are a few hundred characters, which is too thin to quote
evidence from. The three structured tools therefore request the extracted page
body as markdown and build their excerpts from it, falling back to the snippet
when the provider returns none. The analyzer is shown `ANALYSIS_EXCERPT_LIMIT`
of each excerpt, and because evidence quotes must appear in the excerpt, that
limit is exactly what bounds what the model is able to cite. Dimension searches
also request more results, since result count does not change what a search
costs — a wider net simply gives the quality ranking more to choose between.
The snippet-only tools keep asking for snippets.

### Cache reuse

Cache keys are scoped to what actually changes a report: the canonical query,
the market and location, and the tool's own request shaping. They are
deliberately not keyed on the contract fingerprint. The fingerprint covers
goal, offer, CTA intent, and forbidden claims — fields that never reach a
search — so keying on it made every report private to a single generation and
the hit rate effectively zero. Two contracts that ask the same question of the
open web in the same place should share the answer, and the answer is public
web evidence rather than anything client-specific. Market and location stay in
the key because they drive `locality_score` and country targeting, so the same
query in two places is genuinely two reports. In practice a client's second
post reuses the first post's research entirely: four posts for one client cost
twenty-four searches instead of ninety-six.

### Visual references

`VisualReferenceTool` requests observed images from the provider and carries
them as `visual_references` on its report: a URL, an optional description, and
a retrieval timestamp. Descriptions are requested together with the images,
because an undescribed URL is not usable as a reference. Images are never
fetched, stored, or reproduced — the report points at what the market's
advertising looks like and stops there. Only this tool pays for images; the
other seven request text alone, and image collection is part of the cache
variant so the two are never confused.

Images count as evidence: a visual reference report can succeed on images
alone, without text sources. Duplicate and unusable image URLs are skipped like
weak text results, and the list is bounded like every other evidence list.
These remain observations, not art direction — Creative interprets and Design
executes downstream.

### Cost and latency

The three multi-dimension tools issue their dimension searches concurrently
instead of one after another, and the concurrency limit bounds provider calls
rather than whole tools. Holding a slot for an entire tool let Market,
Competitor, and Social block the five single-query tools behind them; the gate
now saturates evenly, so `research_max_concurrency` translates directly into
how many of the twenty-four searches are in flight. A dimension whose search
fails degrades only that angle: the remaining dimensions still produce
evidence, and the lost ones are named in the report's `degraded_dimensions` so
their absence reads as a search failure rather than an empty market. A category
fails only when every one of its dimensions fails.

Evidence is stored once. A finding quotes a bounded lead extract of its source,
cut on a sentence boundary, instead of copying the entire excerpt that already
lives on the source — that duplication was doubling a payload written to
workflow state, cached in Redis, and pasted into every downstream prompt. With
realistic four-thousand-character excerpts this removes roughly forty percent
of the research payload. Per-source confidence now lives on the source itself,
derived from the composite quality score rather than restated per finding.

### Provider failure kinds

A spent plan allowance, a throttle and a broken provider all used to arrive as
one generic failure, so the trace timeline could not tell "top up the plan"
from "wait and retry" from "investigate a bug" — three different answers.
`ProviderQuotaError` (HTTP 402 and Tavily's 432) and `ProviderRateLimitError`
(429) are now their own types, and every other status stays a plain
`ProviderError`. None of them echo the provider's response body.

The reason survives the whole way up: a structured tool whose dimensions all
failed on a spent allowance raises the quota error rather than flattening it
into "every dimension failed", the stage raises it rather than a generic
failure when nothing succeeded, and the measurements count `quota_exhausted`
and `rate_limited` apart from `timed_out` and the failure total. A spent
allowance cannot be short-circuited mid-run — all eight categories are in
flight before the first response returns — so it changes how the stage reports
itself rather than what it spends.

### Measurement

Every failure mode in this stage is quiet by design: a cache hit, a timed-out
category, and a dimension that lost its search all still produce a valid
result. Each run therefore emits a typed `ResearchStageMetrics` — per category
its status, whether it came from cache, duration, confidence, source and
visual-reference counts, degraded dimensions, coverage status and mean source
quality; and per stage the totals, cache hit ratio, timeout count, and the
slowest category that was not served from cache.

Measurements travel through a sink rather than into workflow state, so the
evidence contract stays evidence and downstream prompts do not carry
operational data. `TraceResearchMetricsSink` writes them onto the existing
execution trace timeline — one `tool` record per category and one
`generation_step` record for the stage — so no separate metrics store is
needed and a timed-out category shows as `timeout` rather than a generic
failure. The stage totals are also logged on every run, so the numbers exist
even with no sink wired up. Records carry counts and durations only: no query,
source, provider payload, or client name. A sink that raises is logged and
ignored, because measurement must never be a way for research to fail.

### Time bounds

Three nested bounds keep a hung provider from holding the generation job's
budget, and each degrades into typed evidence rather than a hang.
`RESEARCH_SEARCH_TIMEOUT_SECONDS` bounds one provider call, so a stuck
dimension becomes a `degraded_dimensions` entry while its siblings still
answer. `RESEARCH_TOOL_TIMEOUT_SECONDS` bounds one category, which becomes a
`failed` report with a `TimeoutError` code. `RESEARCH_STAGE_TIMEOUT_SECONDS`
bounds the stage: each tool is given whatever is left of the budget, so
categories that already finished keep their reports instead of being thrown
away by cancelling the whole run. Configuration is rejected unless search fits
inside tool, tool inside stage, and stage inside
`GENERATION_JOB_TIMEOUT_SECONDS`.

Waiting for a concurrency slot is deliberately outside the search timeout, so a
queued call still gets its full budget once a slot frees. The deadline is
monotonic rather than taken from the injectable clock, which is a test seam and
may be frozen.

### Degradation

The eight tools fail independently. A tool that raises produces a typed
`failed` report for its own category carrying a safe error code — the exception
type only, never a provider message, response body, or credential — while the
remaining categories complete normally. `failed` is deliberately distinct from
`no_results`: absence of evidence and absence of research are different claims,
and only the latter is worth retrying. A failed report holds no sources,
findings, or analysis, and the schema rejects any report that mixes the two.
Structured analysis fails loudly rather than silently shipping a report that
lost it, so an unparseable or hallucinated analysis degrades that one category.
When every category fails the service raises instead of returning, so the
Supervisor retries the stage rather than persisting empty evidence.

Only reports that actually carry evidence are cached. Empty and failed reports
are never written, because a cached failure would be served back to the
Supervisor's own retry for the whole TTL and turn a momentary provider problem
into an hour of empty research. Search results that cannot be turned into a
usable source — relative or non-HTTP URLs — are skipped like any other weak
result rather than ending the category.

### Language

Analysis is English end to end: every query, observation, and downstream agent
input is English regardless of the market. Evidence quotes are the deliberate
exception. A quote is verified character for character against its source
excerpt, so it stays in the source language and carries an optional English
`translation` alongside it. Sources written in the market's own language are
first-class evidence. Dimension keyword matching therefore never filters quotes
and never filters insights; topical fit is already established at retrieval
time, since a source may only support a dimension whose own query returned it.
Keyword agreement with the English observation only decides whether an insight
may claim high confidence, and every capped dimension is named in the report's
evidence-coverage limitations rather than dropped silently.
