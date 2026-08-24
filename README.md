# Promotiva

Promotiva is the foundation for an AI marketing platform with a conversational
interface and two future bounded business modules: Posts and Campaigns. This
repository currently provides application bootstraps, infrastructure boundaries,
local containers, health diagnostics, scoped conversation persistence, and
message-linked image uploads. It intentionally contains no AI agents, marketing
workflows, or production prompts.

## Prerequisites

- Docker Desktop with Docker Compose v2
- Optional for host development: Python 3.12/3.13 with `uv`, and Node.js 24 with npm

## Start the complete stack

A local `.env` with generated development-only credentials is created for this
checkout and ignored by Git. For a fresh clone, copy the safe template and replace
each `change_me` value before starting:

```bash
cp .env.example .env
docker compose up --build
docker compose exec -T api alembic upgrade head
```

Run Alembic after the services become healthy and whenever the checkout gains a
new migration. Schema migrations are intentionally explicit and are not run by
the API or worker at startup.

Run in detached mode with `docker compose up --build -d`. Inspect status and logs:

```bash
docker compose ps
docker compose logs -f api
```

Open the frontend at <http://localhost:3000>. API liveness is available at
<http://localhost:8000/api/health>, and dependency readiness at
<http://localhost:8000/api/health/ready>. Readiness verifies PostgreSQL,
the pgvector extension, Redis, and the configured S3-compatible bucket.

## Service ports

| Service | Port | Purpose |
| --- | ---: | --- |
| Frontend | 3000 | Nuxt development UI |
| API | 8000 | FastAPI and OpenAPI |
| PostgreSQL | 5432 | PostgreSQL 17 + pgvector |
| Redis | 6379 | cache and future queue coordination |
| MinIO S3 | 9000 | object storage API |
| MinIO console | 9001 | local storage administration |

The worker exposes no port. `minio-init` creates the configured private bucket and
then exits successfully; rerunning it is safe.

## Stop and reset

```bash
docker compose down
```

To also delete PostgreSQL, Redis, MinIO, and development dependency volumes:

```bash
docker compose down -v
```

The `-v` command permanently removes locally persisted Docker data.

## Host checks

```bash
cd backend
uv sync
uv run pytest
uv run ruff check .

cd ../frontend
npm ci
npm run typecheck
```

## Module boundaries

Promotiva is a modular monolith. HTTP routes depend on application services, which
depend on module contracts and infrastructure abstractions. Posts and Campaigns
own their internals. Campaigns may later request Posts work only through a public
Posts service contract, never by importing Posts agents or tools. Shared concepts
belong in `app/shared` only when both modules genuinely use them.

See [architecture overview](docs/architecture/overview.md),
[Posts boundaries](docs/architecture/posts.md), and
[Campaigns boundaries](docs/architecture/campaigns.md).

## Conversation scope

Conversation endpoints require `X-User-ID` and `X-Project-ID` UUID headers. The
API uses both values in every conversation and message query so a conversation
identifier alone cannot cross a user or project boundary. These headers are the
current trusted identity-boundary contract and must be populated from verified
authentication context in a production deployment, not arbitrary client input.

## Asset uploads

`POST /api/assets` accepts a multipart image with `message_id`, `role`, and
`file`, plus the same `X-User-ID` and `X-Project-ID` scope headers. JPEG, PNG,
and WebP content is inspected from its bytes before it is written to the private
S3-compatible bucket. Metadata is available through `GET /api/assets/{id}` or
`GET /api/assets?message_id={id}`. Repeated content is checksum-deduplicated
within a project while each attachment remains linked to its message.

## Posts domain

The Posts API creates standalone, conversation-backed, or future campaign-backed
posts. Each post owns ordered generation attempts and each attempt starts in
`pending`; later worker tickets will drive queueing and workflow transitions.
Generation artifacts store scoped metadata and private storage references without
exposing internal object keys through HTTP responses.

Every generation also owns a durable workflow state. It starts at version 1 and
stores the conversation context, brief, semantic contract, brand/product inputs,
assets, audience/research outputs, strategies, creative and design decisions,
generation references, quality results, and revision history. A stage updates one
section with an expected version; stale writes return `409`, while every accepted
write creates an immutable snapshot that a restarted worker can recover.

Before downstream work begins, a generation can establish its semantic contract
through the dedicated Posts API. The contract protects the named company, brand,
product, primary entity, objective, audience, market/location, offer, CTA,
platform, language, required facts/assets, forbidden claims, and constraints.
It is fingerprinted and write-once; deterministic validation returns `HARD_FAIL`
instead of allowing a later stage to silently change product or offer truth.

The Posts agent runtime and tool registry are internal worker boundaries, not
public tool-execution endpoints. Every registered agent and tool declares typed
input/output schemas, bilateral allowlists, timeout and retry policy. Unauthorized
calls fail closed and emit correlation-aware audit events without payload data.
