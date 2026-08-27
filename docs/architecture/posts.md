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
       -> PostGenerationJob exactly 1
       -> GenerationArtifact 0..N
       -> PostGenerationState current version 1..N
            -> PostGenerationStateVersion snapshot 1..N
```

Generation status is explicit (`pending`, `queued`, `running`, `reviewing`,
`revision`, `completed`, `failed`, or `cancelled`). Creating an attempt atomically
creates a durable queued job and does not run AI work in the HTTP request. A
nullable `campaign_id` keeps standalone and future Campaign-created posts on the
same Posts model.

## Durable generation queue

PostgreSQL is the source of truth for generation jobs. Each generation owns one
job ID, a bounded attempt count, execution timeout, availability time, worker
lease, safe error code, and terminal state. Workers claim with row locking and
`SKIP LOCKED`, so multiple workers cannot execute the same available job. A
retryable failure is scheduled with backoff; exhausted retries become `dead`,
while explicitly non-retryable errors become `failed`.

An interrupted worker leaves a `running` job with a lease. Once that lease
expires, another worker can reclaim the same persisted generation and resume
from its versioned state. No request payload or in-memory queue is required for
recovery. `Idempotency-Key` is hashed with the Posts scope and post ID; retrying
the same HTTP generation request returns the original generation and job rather
than creating a duplicate. Ticket 10 supplies the executor boundary; Ticket 11
connects the resumable Post Supervisor executor to it.

The workflow state contains a protected Supervisor checkpoint plus explicit
sections for conversation context, brief,
semantic contract, brand, product, assets, audience, research, marketing
strategy, creative concept, copy, art direction, design specification,
generation plan and artifacts, quality, and revision history. A write changes
exactly one section, validates whether it is an object or array, and uses an
`expected_version` compare-and-swap. This makes concurrent stage writes safe and
lets a worker resume from PostgreSQL after restart instead of relying on implicit
LLM memory. Full snapshots are retained for every accepted version.

## Post Supervisor

`PostSupervisor` is a deterministic control-plane over a declared dependency
graph. It returns structured `CONTINUE`, `SKIP`, `RETRY`, `REVISE`, or
`STOP` decisions with a next stage, reason, required inputs, and state
requirements. It does not perform specialist work and does not import providers,
SQLAlchemy, or Campaign internals.

`PostSupervisorExecutor` implements the durable generation executor boundary.
It loads the latest PostgreSQL checkpoint, persists every routing decision using
optimistic state versions, invokes only a registered stage handler, validates
that handler outputs match the stage's declared sections, and marks the stage
complete. Existing outputs are skipped; incomplete attempted stages are retried
within their stage policy; pending revisions route to the smallest declared
stage; and quality hard failures terminate. Completion still requires an
explicit quality `PASS`.

Supervisor progress records current/completed/skipped and revision-invalidated
stages, requested optional skips, per-stage attempts, and the latest decision.
A targeted revision invalidates only its stage and transitive downstream
dependents so stale outputs are recomputed and quality review runs again. This
makes restart recovery
state-driven rather than dependent on an in-memory list of calls. The checkpoint
is protected from the public generic state-write operation. The semantic
contract remains immutable and is validated before persistence. Specialist stage
handlers are introduced by their own tickets.

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
a mismatched fingerprint is a hard failure. The Supervisor routes on this
structured decision but does not own or rewrite the contract.

## Client Understanding

`ClientUnderstandingAgent` is the first specialist stage. It reads typed
conversation turns, the latest client message, attachment metadata, and existing
project facts from `conversation_context`; it writes only `brief`. Its contract
captures business, brand, product/service, objective, audience, market,
location, platform, language, offer, CTA intent, style preferences, constraints,
assets, and missing fields.

The deterministic Missing Information & Clarification Engine evaluates those
missing fields immediately after understanding. It persists classifications and
at most three critical questions under `brief.clarification`. Optional,
inferable, and researchable gaps do not interrupt the user. The Supervisor reads
the plan and blocks only when critical clarification is pending; it does not own
the classification policy.

The provider is asked for strict JSON through the LLM interface. Pydantic rejects
unknown fields, so this stage cannot silently add positioning, marketing
strategy, creative concepts, copy, or design direction. Verified project facts
take precedence over model output. Asset IDs, roles, filenames, and identity
preservation flags are reconstructed from trusted input, preventing attachment
hallucination or substitution. Invalid structured output follows the bounded
AgentRuntime retry policy and is observable through AgentRun and ProviderCall
traces.

## Brand and Product analysis

`BrandProductStrategistAgent` runs after the immutable semantic contract exists.
It reads no provider-specific objects and produces only the `brand` and `product`
state sections. Authoritative identity, offer, required facts, forbidden claims,
required assets, constraints, and contract fingerprint are copied from the
validated contract at the application boundary.

The model may reason about identity, personality, benefits, customer value, and
USP candidates, but every feature and USP must cite a real required-fact key.
Every required fact must be classified as brand or product information. The
strict provider-output schema contains no audience research, positioning,
marketing strategy, creative concept, copy, art direction, or design fields.
Unsupported facts and forbidden claims are rejected before workflow persistence.
The specialist writes internal analytical prose in English for stable model
quality, but preserves authoritative proper nouns, offers, and fact values
verbatim. This does not change the semantic contract's requested language; final
localized content remains a downstream Copywriter responsibility.

## Audience Intelligence

`AudienceIntelligenceAgent` runs after Brand & Product analysis and writes only
the `audience` state section. It goes beyond demographics by producing audience
segments, a selected target, needs, desires, pain points, objections,
motivations, purchase intent, trust triggers, situational context, and a
current-state/desired-state customer tension.

Each derived value cites an allowlisted basis from the semantic contract or the
validated Brand/Product outputs and carries a confidence level. Unknown evidence
references, protected-state drift, forbidden claims, and strategy/copy fields
fail closed. The output explicitly records that these are reasoned hypotheses;
Ticket 18 External Research may validate or enrich them, while the Marketing
Strategist remains responsible for positioning and strategic decisions.

## Asset Intelligence

`AssetIntelligenceAgent` runs after the immutable semantic contract and writes
only the `assets` state section. It classifies every attachment into the Posts
asset vocabulary and retains the exact user quote whenever conversation intent
changes an otherwise ambiguous classification. Declared logo, product,
packaging, environment, background, style-reference, and inspiration roles are
authoritative and cannot be downgraded by a provider response.

The model proposes classification only. The application deterministically owns
`required`, `preserve_identity`, `allow_crop`, `allow_replace`,
`allow_generation`, `min_dominance`, and `max_dominance`. Logo, primary product,
vehicle, and packaging identity cannot be replaced or synthesized. Required
asset IDs are checked against the workflow attachments before provider use.

Downstream composition and verification submit typed usage assertions to the
asset-policy gate. Missing required assets, unauthorized replacements or
generated substitutes, identity drift, forbidden cropping, and out-of-range
dominance produce `ASSET_POLICY_HARD_FAIL`; replaceable background and
inspiration references may continue according to their explicit policy.

## External Research

`ExternalResearchService` is the central Ticket 18 orchestration boundary. It
runs `MarketResearchTool`, `CompetitorResearchTool`, `AudienceResearchTool`,
`SocialResearchTool`, `VisualReferenceTool`, `TrendResearchTool`,
`PlatformResearchTool`, and `BrandProductResearchTool` with bounded concurrency.
Each tool depends on provider-neutral ports and returns the same typed report
envelope. Three Ticket 19 tools additionally use the provider-neutral LLM port
to convert untrusted search excerpts into strict market, competitor, and social
analysis schemas.

Reports contain a canonical query, status, provider, optional provider summary,
source-linked findings, source excerpts, retrieval and expiry timestamps,
confidence, and a deterministic cache key. Confidence is derived from provider
scores rather than invented by another model. Duplicate URLs are removed and an
empty search remains a valid, low-confidence `no_results` report.

The cache boundary is injected. In-memory caching supports deterministic tests;
`RedisResearchCache` provides cross-worker TTL reuse in production composition.
Cache errors degrade to live research rather than discarding successful external
evidence. The Supervisor stage validates semantic-contract and Audience
Intelligence identity before any provider call and writes only `research`.
Research remains evidence: Marketing Strategy decides, Creative interprets, and
Design executes.

Requests are targeted rather than generic. Each tool declares its provider
search index, recency window, geo-targeting, and pinned domains, so market
tools narrow to the resolved country and platform research reads the platform's
own documentation. Trend research deliberately does neither: measured live, a
query naming the exact entity and market on the news index returned one usable
source in five where the general index returned five, so recency is ranked
through freshness rather than filtered for, and the market is applied by the
fit gate instead of by the index. Markets the provider
cannot express resolve to no country instead of a neighbouring one, with Kosovo
deliberately mapped to Albania and reconciled afterwards by `locality_score`.

Source confidence follows a weighted composite of relevance, authority,
locality and freshness. Locality folds diacritics and compares stems so
local-language pages are recognised as local, and ignores generic geography
words. Freshness is optional: an unknown publication date redistributes its
weight across the measured signals rather than scoring as stale.

Structured tools request extracted page bodies rather than search snippets, so
the analyzer has real text to quote, and dimension searches request more
results because result count does not change a search's cost. Cache keys are
scoped to the canonical query, the market and location, and the tool's request
shaping — never the contract fingerprint, which covers fields that never reach
a search and made every report private to one generation. Reports are therefore
reused across generations of the same client and market.

`VisualReferenceTool` declares image collection, so the provider port carries
observed images and the report exposes `visual_references` with URL,
description, and retrieval time. A visual reference report may succeed on
images alone. Images are referenced, never fetched or stored, and remain
research evidence rather than art direction.

Dimension searches inside a structured tool run concurrently, and the bounded
concurrency applies to provider calls rather than to tools, so multi-dimension
tools no longer block single-query ones. A failed dimension is recorded in
`degraded_dimensions` and loses only that angle; a category fails only when all
of its dimensions do. Findings carry a bounded extract of their source rather
than a second copy of the excerpt, keeping the persisted research section from
storing the same evidence twice.

Provider failures are typed by kind. A spent plan allowance and a throttle are
distinct from a generic failure because the operational response differs, and
the distinction is preserved through dimension aggregation, the stage-level
raise, and the recorded measurements.

Each run emits typed stage and per-category measurements — status, cache hit,
duration, confidence, source counts, degraded dimensions, and coverage — through
a sink rather than into workflow state, keeping the evidence contract free of
operational data. The orchestration layer adapts them onto the execution trace
timeline as one tool record per category plus a generation-step record for the
stage. A failing sink is logged and ignored.

Time is bounded at three nested levels — one provider call, one category, and
the stage — each configured and cross-validated against the generation job
timeout. A call that overruns degrades its dimension, a category that overruns
becomes a `failed` report, and an exhausted stage budget leaves already
completed categories intact because each tool is bounded rather than the whole
gather.

Tools degrade independently. A failing tool yields a typed `failed` report for
its category with a safe error code, distinct from `no_results`, while the
other categories complete; the service raises only when every category fails,
leaving stage retry to the Supervisor. Empty and failed reports are never
cached, so a transient outage cannot be served back to the retry that was meant
to recover from it.

The pipeline is English: queries, observations, and downstream inputs are
English for consistent model quality. Evidence quotes stay verbatim in their
source language because they are verified against the source excerpt, and carry
an optional English translation. Topical fit is enforced by retrieval, not by
keyword-matching free text, so local-language evidence is never discarded;
keyword agreement only caps an insight's confidence, and the cap is reported in
evidence-coverage limitations.

Market analysis explicitly separates category context, expectations, offers,
positioning patterns, and evidence-supported opportunities. Competitor analysis
separates messaging, offers, CTA, visual language, differentiation, and overused
patterns. Social analysis separates platform patterns, text density, CTA, logo
placement, photography, graphic systems, and compositions. Every dimension uses
a focused query. Low-relevance results are filtered, and each retained source is
scored for authority, locality, freshness, and overall quality. The model cites
only temporary source IDs and exact evidence quotes; the application verifies
the quote against the source excerpt and checks that the source was collected
for that dimension. A quote that is verbatim in a different supplied source is
re-attributed to the source it came from: models copy spans correctly and then
mislabel them, and the span decides which page it belongs to. A quote that
matches nothing is dropped along with its own evidence item and reported in
evidence-coverage limitations, so one bad citation costs itself rather than the
whole category; a response that grounds nothing at all still fails. Unsupported
observations are omitted, and the report names missing dimensions through an
evidence-coverage contract.

Visual reference, trend and platform are analyzed on the same terms. Visual
reference separates composition, subject scale, negative space, text density,
headline region, typography, photography, lighting, colors, CTA, logo, graphic
elements, energy, and texture; it describes creative that was observed and is
never design direction, and it still collects referenced images, which are
evidence on their own and can carry the category without a single text source.
Fourteen attributes do not cost fourteen searches: dimensions answered by the
same kind of page declare the same query, identical queries are searched once,
and every dimension that asked is credited with the result, so provenance
survives the saving.

Trend separates current, emerging, overused, and declining, collecting what to
avoid as deliberately as what to adopt. Each trend is judged three times and
independently — brand fit, audience fit, objective fit — against the brief's
own goal, which the research context carries for this purpose. Usability is not
a model's to assert: like a competitor report's safe_use, it is computed from
the three fits, and a trend is usable only when all three hold. A trend that
fits none of them is still reported, because knowing a trend is wrong for this
brief is evidence too. Platform separates the formats a platform supports from
the constraints it publishes, and searches the platform's own documentation
rather than whoever ranks for it locally.

Excerpts are filtered before they are ever shown. Runs of link-only lines are
navigation rather than evidence, and the span the analyzer sees is ranked by
market relevance and price density rather than taken from the front of the
page, which on aggregator sites is a language picker. Selection never invents
or reorders text, so quotes stay verifiable against the untrimmed excerpt, and
the parts it joins are separated by an explicit break the model is told not to
quote across. Search excerpts are treated as untrusted input. Competitor research is tagged
`differentiate_do_not_copy`, and any instruction to copy or replicate a
competitor fails validation.

## Marketing Strategy

The Marketing Strategist is the first stage that decides rather than gathers.
It reads the understood brief, semantic contract, brand and product analysis,
Audience Intelligence and all eight research reports, and returns twelve decisions:
business objective, segmentation, targeting, positioning, customer insight,
customer tension, USP, value proposition, marketing angle, single-minded
message, desired reaction, and CTA strategy, plus the message framework.

Every decision carries its reasoning and what that reasoning rests on. The
rationale is a required field rather than a request in a prompt, because a
strategy whose reasoning is optional degrades into assertions, and an assertion
cannot be reviewed or corrected by a later stage. Each decision cites basis
identifiers drawn from an allowlist built from the inputs themselves, the same
mechanism Audience Intelligence uses: a decision that cannot be grounded in
supplied evidence is a decision the strategist may not make.

Allowlisting alone is not treated as grounding. Each decision has a mandatory
evidence shape: positioning needs a supplied target and verified product value;
customer insight and tension need audience evidence; USP needs a product
feature-benefit chain or candidate; value proposition, angle and message need
both product value and audience evidence; CTA needs the declared intent and
objective. Deterministic gates additionally reject unknown named entities,
unsupported numbers, prices, guarantees, free benefits and superlatives. This
prevents a valid basis identifier from laundering an invented claim.
The minimum provenance set is application-owned: exact model-selected IDs are
preserved, unambiguous prefixes are normalized, unsupported IDs are discarded,
and missing mandatory foundations are attached deterministically from the
upstream allowlist. The model therefore owns the decision and rationale, not
fragile citation bookkeeping; semantic and claims gates still fail closed.

The principles are structural, not decorative. Each field is bound to the
discipline it answers to — segmentation and targeting to STP, positioning to
positioning, the USP to USP — and a decision that labels itself as a different
discipline is rejected, because a targeting call labelled "positioning" has not
made a positioning decision. Targeting must name one of the supplied audience
segments; the USP must descend from a supplied product feature-benefit chain or
USP candidate, so it states what this product verifiably does better rather
than what sounds appealing; the customer tension must build on the tension
Audience Intelligence found; the business objective belongs to the brief's goal
and the CTA strategy to the declared CTA intent. The single-minded message must
be one sentence, since two sentences are two messages and a post carrying two
carries neither; additive multi-promise forms are rejected as well. PAS requires
a supplied pain point or tension, while AIDA requires both objective and audience
context. Either may be declined, which is why "none" is a first-class answer.

Seven read-only Marketing Strategist tools provide the reusable framework
scaffolding: STP, positioning, feature-to-benefit mapping, USP extraction,
value proposition, message strategy and CTA. They expose grounded options and
constraints; the agent still owns each final decision and rationale. The tools
run through the authorized registry, emit normal tool traces, reject semantic-
contract drift and never mutate state or call an external provider.

Assembling five upstream outputs is the first point where they could silently
disagree, so every input's contract fingerprint is checked against the contract
before any provider call. Evidence gaps travel with the strategy: audience
limitations and missing research dimensions are carried onto the output, so a
later stage inherits the uncertainty rather than only the decisions. Forbidden
claims fail the whole strategy, and copy, headlines, art direction and design
remain out of scope: this stage produces the thinking that later stages execute.

## Semantic memory

Posts semantic memory is an internal application service backed by PostgreSQL
and `pgvector`. It accepts only brand knowledge, approved creatives, research
summaries, successful concepts, visual references, designer feedback, rejected
concepts, and rejected patterns. IDs, statuses, timestamps, and operational or
user-record data remain relational fields or JSON metadata and are never used as
the vector text. Repeating the same content, kind, and partition is idempotent.

Every record belongs to one exact partition inside a mandatory `user_id`
boundary: one brand, one project, one normalized category, or the user's global
partition. Category and global memories must be explicitly brand-neutral.
Retrieval applies the user, level, and partition key filters in SQL before
ordering by cosine distance; it never broadens a brand query to another brand,
category, project, user, or global partition. Callers that want multiple levels
must request each permitted partition explicitly and merge the results at their
own application boundary.

Embeddings use a fixed 768-dimensional schema and cosine HNSW index. Provider
and model identity are retained on every memory. A provider dimension mismatch
fails before persistence or retrieval instead of writing incompatible vectors.
## Creative Direction

The Creative Director converts an approved marketing strategy into exploration,
not a final poster. It reads the marketing strategy, audience intelligence,
brand analysis, research and semantic contract. All five contract fingerprints
must agree before the provider is called, and its only workflow write is
`creative_concept`.

Each run produces three to five creative territories, visual hooks and Big Idea
candidates. Every territory declares the angle it enters through — emotional
transformation, visual metaphor, cultural tension, product demonstration or
brand symbol — and no two may share one, because three renamings of the same
promise are one route wearing three names. A visual hook carries a symbol and a
wordless read: what the image says with every word removed. A Big Idea names one
territory and one hook, states what it adds to each, and lists the further
executions it would carry; an idea that only supports the post in front of it is
an advertisement.

The chain from audience tension to marketing angle, territory, Big Idea and hook
is checked link by link. Each link must interpret the one above it and introduce
something that step did not contain, so a chain of synonyms cannot pass as
reasoning.

Scoring is held to the same standard. Each candidate is scored one to ten on the
eight selection dimensions: strategy fit, audience fit, brand fit, originality,
clarity, visual potential, platform fit and production feasibility. Ticket 24's
territory differentiation, claim safety and concept-hook alignment remain
quality gates rather than silently changing the ranking. Every candidate names
its own weakness. A flawless card and two identical selection cards are both
rejected: an evaluation that separates nothing decides nothing. The application
ranks by the eight-dimension total and deterministic dimension priority, so the
winner is always separated by a judgement rather than by list position.

The persisted output publishes `winning_concept` plus every non-winner under
`rejected_concepts`, retaining rank, total score, weakness and a comparison-based
rejection reason. Before generation, the stage retrieves only project-scoped
`rejected_concept` semantic memories and supplies them as anti-repetition
context. After selection, every losing route is stored through the semantic
memory service; concept language is embedded, while generation/candidate IDs and
scorecards stay JSON metadata. Worker retries are idempotent because the same
partition, kind and semantic content share one memory record.

Quality gates reject renamed versions of the same route, close paraphrases of
the approved strategy wording, stock product shots offered as hooks, and
audience-facing advertising copy disguised as a concept. Unsupported absolute or
numeric claims, forbidden claims, identity replacement and competitor copying
fail closed. Sanitization after a local-model repair drops whole sentences
rather than deleting words in place, because a field edited token by token comes
back safe and meaningless; a field with nothing sayable left comes back empty
and fails the run instead of shipping a placeholder. Every repaired output faces
the full bar again, including the gate, so nothing is waived for being a repair.
Small serialization drift, such as detached scorecards or renamed fields, is
normalized without rewriting creative content or choosing a territory's angle
for the provider.

This is the first stage that invents rather than extracts. Every earlier stage
answers to evidence placed in front of it, which a small model does well; a Big
Idea has nothing to read off. `CREATIVE_LLM_MODEL` points this stage at a
stronger model, and left empty every stage shares `LLM_MODEL`. The bundle
carries the second provider so the choice stays a deployment decision: no agent
selects its own model, and traces name the model from the response either way.

The boundary to downstream specialists is explicit. Split screens, overlays,
animations, logo or tagline placement, headlines, captions, CTA copy, typography,
dimensions, image prompts and final posters are not Creative Director output.
The agent has no tools, approval capability or mutation capability; Copywriter,
Art Director and production stages remain responsible for execution.

## Copywriting

`CopywriterAgent` runs after concept selection and writes only the `copy` workflow
section. It receives the approved marketing strategy, winning concept, brand
voice, platform, offer and immutable semantic contract. Rejected concepts are
deliberately excluded from its source: they inform future anti-repetition, not
the copy for the selected route. Strategy, concept, brand and contract
fingerprints must agree, while platform and offer must exactly match the
semantic contract before a provider is called.

The provider returns only headline, subheadline, supporting copy, optional offer
copy, CTA, caption and optional hashtags. The application owns the final quality
record and checks clarity, tone, length, grammar, claim validity, text density
and mobile readability. Headline and CTA word counts, sentence length, caption
limits, overlay density, capitalization, punctuation and hashtag shape are
deterministic gates rather than model self-assessment.

Approved offer wording is preserved exactly. Numeric claims, prices,
percentages, guarantees, free benefits, superlatives, availability promises and
forbidden claims must already exist upstream; otherwise the complete output is
repaired once and then fails closed. Copywriting cannot emit layout, typography,
logo placement, image prompts or any other Art Director or production field.

## Art direction

`ArtDirectorAgent` runs after copywriting and asset intelligence, and writes only
the `art_direction` workflow section. It receives the winning concept, approved
copy, verified brand analysis, asset policies, platform and immutable semantic
contract. All upstream fingerprints must agree before the provider is called.
Rejected concepts are excluded so the selected route remains the only creative
source of truth.

The output defines focal point, composition, ordered visual hierarchy, product
dominance, negative space, photography, lighting, typography, color, graphic
language, CTA treatment and protected logo region. Product leads the hierarchy;
headline, approved offer and CTA follow; logo closes it. Deterministic gates
enforce hierarchy, concept alignment, asset fidelity, copy fit and mobile
readability. Product dominance must respect both global bounds and any supplied
asset-policy range. Unsupported color codes and instructions that replace or
regenerate protected product or logo identity fail closed after one full repair.

This stage describes production-ready visual intent but does not generate an
image, rewrite copy, create SVG/CSS or emit a final layout. It has no tools and
cannot mutate assets.

## Design specification

`DesignSpecAgent` is the typed compiler between Art Director and Composer. It
reads the approved art direction, copy and semantic contract, then writes only
the `design_spec` workflow section. Composer-facing code must consume this
contract and must not interpret the Art Director's free-form prose directly.

DesignSpec schema version `1.0` represents canvas and safe-area dimensions in
pixels, grid columns/rows/gutters/baseline, named product/headline/offer/CTA/logo
regions, typography roles, color tokens, graphic elements, and production
directions for photography, lighting and background. Unknown fields are
rejected. Text and logo regions must fit the safe area; all geometry must fit
the canvas; and offer geometry and typography exist only when approved offer
copy exists. The semantic-contract fingerprint is attached by the application,
not supplied by the model.

Invalid structured output receives one complete repair pass and then fails
closed. The compiler emits neither rendered assets nor CSS, SVG, image prompts
or final composition, and it has no tools.

## Layout engine

The deterministic design toolset converts a validated `DesignSpec` into a
versioned `LayoutPlan`. `SafeAreaEngine` clamps protected content,
`GridEngine` produces concrete track lines and snaps bounds, `SpacingEngine`
enforces baseline rhythm, and `VisualHierarchyPlanner` assigns stable priority
and visual flow. `LayoutEngine` coordinates those tools without an LLM call or
mutation of the source specification.

Every placement exposes pixel `x`, `y`, `width`, `height`, alignment, priority,
z-index and machine-readable constraints. The plan also records spacing
relations and measurable alignment, balance, whitespace, scale, rhythm,
proximity, Gestalt grouping, focal point and visual flow. Product may use the
canvas edge; headline, offer, CTA and logo remain inside the safe area. This is
layout planning only and does not render or generate production assets.

## Typography engine

Typography is deterministic and never delegated to an image model. The
`TypographyEngine` combines approved copy, typography roles, color tokens and
the concrete LayoutPlan. It outputs exact text blocks with family token, weight,
font size, line height, letter spacing, line breaks, maximum lines, measured
text width, alignment, priority, bounds, contrast ratio and fit status.

Headline, subheadline and supporting copy share the planned headline group;
offer, CTA and optional legal text use dedicated regions. The fitter preserves
copy exactly and reduces size only down to role-specific readability floors.
WCAG-style contrast thresholds are applied to normal and large text. Overflow,
unavailable fonts, overlap, clipping, unreadable contrast and text outside the
safe area are hard failures. The engine is pure and repeatable: it calls no
provider, performs no rendering and does not mutate DesignSpec or LayoutPlan.

## Color and contrast engine

`ColorContrastEngine` resolves the DesignSpec color tokens into dominant,
secondary, accent, background, text and CTA roles. Tokens marked as brand colors
must exist in an explicit approved brand palette; colors marked neutral are
checked for low chroma so an invented color cannot bypass brand validation.

Text/background and CTA contrast use deterministic relative-luminance ratios,
and remain consistent with the TypographyPlan. Product color samples are
compared with the background; an unusably low separation is a hard failure,
while a marginal result requires an approved neutral separation treatment. The
engine also reports a bounded visual-harmony score and preserves the objective
and mood that the palette is expected to support.

Gradients are absent by default. A gradient is accepted only when explicitly
approved, composed solely of approved/resolved palette colors, and its reason is
grounded in the supplied objective or mood. “Looks modern” is not a valid design
reason. The engine is deterministic and invokes no image or text provider.

## Generation planning

`GenerationPlanner` is the deterministic gate before any image-provider call.
It inventories classified assets and explicitly records what is available,
missing, identity-protected and permitted to be generated. The stage reads the
semantic contract, DesignSpec and asset policies, then writes only the
`generation_plan` workflow section.

When a useful focal visual and background already exist, the decision is
`COMPOSE_ONLY` with zero image calls. A useful visual without a background yields
`GENERATE_BACKGROUND`; the original product and logo remain composition assets.
When no useful visual exists, `GENERATE_SCENE` creates only unbranded scene
context. Generated promotional text, headline, offer/price, CTA, logos,
watermarks and replacement products are prohibited in every generation task.

The plan records the estimated image calls and cost tier, making fidelity,
latency and cost consequences visible before production. Planning invokes no AI
provider and is repeatable for identical state.

## Image prompt builder and scene generation

`ImagePromptBuilder` compiles the immutable semantic contract, selected creative
concept, approved art direction, `DesignSpec`, asset policies and generation
plan into a provider-neutral `ScenePrompt`. All inputs must carry one contract
fingerprint. Protected product and logo policies become exact quiet-region
reservations from the design geometry; asset identifiers and customer-facing
brand, product, offer and CTA strings never enter the provider prompt.

The positive prompt asks only for a scene or background plate: environment,
lighting, photography, atmosphere and texture. A complete negative prompt
always prohibits readable promotional text, fake logos, fake branding, fake
prices or offers, CTA, UI and watermarks. Final copy, prices, CTA and logos are
therefore left to deterministic composition rather than the image model.

`ProductionStageHandler` skips the provider for `COMPOSE_ONLY`. Otherwise it
calls the provider through the existing interface, rejects empty, unreadable,
unsafe, MIME-mismatched or incorrectly sized output, and persists validated
bytes through `StorageProvider`. Workflow state receives JSON-only artifact
metadata: deterministic storage key, checksum, dimensions, provider/model and
prompt fingerprint. The key is stable for a generation and prompt, so worker
retry overwrites the same artifact instead of creating duplicates. Provider or
storage failures remain stage failures for the Supervisor retry policy; no
invalid artifact metadata is committed.

## Product preservation and image editing

A generated stand-in for a real vehicle, package or logo is not a rendering
mistake; it is a false claim about what the customer sells. `ProductPreservationPipeline`
is therefore built to be incapable of one. Its nine tools — masking, background
removal, crop, perspective handling, lighting adaptation, edge cleanup, scale,
placement and shadow integration — derive the subject from the uploaded pixels.
Resampling, exposure adjustment and the external shadow necessarily create
derived pixels, but the pipeline cannot generate or substitute the promoted
subject and has no image provider to ask.

Permission is answered before a single byte is decoded. An asset carrying
`preserve_identity` can never be swapped for another asset or stood in for by
generated bytes, and the same refusal applies whenever the policy has not
granted `allow_replace` or `allow_generation` — so a vehicle, product,
packaging or logo nobody authorised for replacement does not become replaceable
because that one flag happens to be off. Cropping needs `allow_crop`. Each
refusal carries a `PreservationFailure` code, raised from the pipeline rather
than from a validator, because pydantic would otherwise bury the code a caller
needs to tell "this may not be replaced" from "these bytes are not an image".
Even when policy permits replacement, selection of the replacement belongs to
the asset-selection boundary; this pipeline refuses to silently ignore or
pretend to execute such a request.

The edits are bounded to what keeps a product recognisable. Scaling preserves
aspect ratio, since a squashed car is a different car. Lighting adapts
brightness within a tenth either way and never touches hue. Perspective
correction straightens a photo taken at a slight angle and stops well short of
restyling. Background removal floods inward from the canvas edges, so colour
the product encloses — a window, a label panel — keeps its pixels. The flood-fill
reachability marker uses alpha rather than a possible RGB product colour, so a
near-black product is not mistaken for the backdrop. An asset masked away to
nothing fails rather than composing an invisible product.
When corner colours indicate a complex photographic gradient, deterministic
background removal fails with `BACKGROUND_REMOVAL_UNSAFE` instead of risking
damage to the promoted subject. The caller must retain the background or
provide a trusted, source-sized mask; a dedicated segmentation adapter can be
introduced later without weakening this preservation-first fallback.

The result reports each of the nine operations exactly once, the source and
output digests that let a later stage prove the pixels descend from the upload,
and the share of the canvas the asset actually covers, measured through its
alpha rather than its bounding box. Structured fidelity evidence records why
the result can claim descent from the upload; the policy flag itself is never
used as evidence. `usage_assertion()` turns that into the
`AssetUsageAssertion` the existing policy validator consumes: the pipeline
states what it did and lets `evaluate_asset_usage` decide whether the policy was
satisfied, instead of certifying itself.

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

## AI provider abstraction

Posts agents and tools depend only on typed provider ports owned by the Posts
module: LLM, vision, image, embedding, research, and storage. Request and
response contracts include provider/model identity where relevant but contain no
SDK-specific objects. Technical adapters live under `app/integrations` or
`app/infrastructure`; provider SDKs and HTTP response shapes never enter agents.

A vision request may carry the JSON Schema its answer must satisfy. Adapters
that support constrained decoding enforce it during generation; the rest ignore
it and rely on prompt shaping, so the schema is a guarantee where it can be one
and never a requirement on the port. This is what makes a structured vision gate
affordable on a local model: asked in prose, a reasoning vision model spends an
order of magnitude more tokens on its private trace than on the answer, and the
gate exhausts its request timeout before replying.

The application composition root selects each adapter independently from
environment configuration. Current adapters support Ollama for text, vision,
and embeddings; Hugging Face Inference Providers for image generation; Tavily
for research; and S3-compatible storage for MinIO/S3. A deterministic mock exists
for every port, allowing workflow tests to run without network calls or secrets.
Unknown or incomplete configuration fails closed with safe errors.

## Hard verification gates

Twelve gates stand between a finished render and the client, and none of them is
a score: `correct_brand`, `correct_product`, `correct_logo`, `correct_offer`,
`correct_spelling`, `required_facts_present`, `required_assets_present`,
`forbidden_claims_absent`, `fake_branding_absent`, `unwanted_text_absent`,
`correct_dimensions` and `asset_fidelity`. One failure sets the decision to
BLOCKED and the Supervisor terminates the workflow, whatever the marketing and
design reviews concluded. The gates run directly after composition, before
anything scores the post, so a blocked render never costs a review and no score
exists to be pointed at afterwards.

Every gate is decided in policy from the semantic contract, the approved copy,
the design spec and the draft's own component record. The vision model is a
witness: it enumerates the legible strings, the brand identities and the
depicted products, and never judges whether the post is acceptable. Constrained
decoding holds it to that shape. The same inputs therefore always yield the same
verdict, and a blocked post carries the evidence each gate read.

Text gates compare the rendered strings against the approved copy rather than
against a dictionary, which catches truncation, substitution and dropped glyphs
in any language. Evidence read off the image is matched tolerantly, so a small
model's misreading of approved copy cannot block a post while genuinely foreign
text still does. This stage never requests a revision: a hard gate that could be
negotiated with would be a score with extra steps.

## Observability and execution tracing

The Posts execution boundary records four trace kinds: `agent`, `tool`,
`provider`, and `generation_step`. They share one ordered timeline keyed by
generation and correlation ID. The timeline records name, terminal status,
duration, retry count, safe error code, provider/model, token counts and cost
when known. Stage records correspond to Supervisor stage handlers, so timings
such as Understanding, Research, Strategy, Generation, Composer, and Vision QA
can be compared without reading application logs.

Trace inputs and outputs are non-reversible `sha256:` references. Raw briefs,
prompts, generated bytes, provider bodies, access tokens, and API keys are not
persisted. Telemetry persistence is isolated from workflow transactions and a
telemetry failure is logged without masking the actual agent, tool, provider, or
generation result. The public read endpoint remains scoped by the owning user
and project through the Post repository boundary.

Other modules must enter Posts through a public module-level application service,
such as `PostGenerationService`. They must not import Posts agents, tools, or
orchestration internals.
