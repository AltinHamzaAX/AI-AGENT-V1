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
