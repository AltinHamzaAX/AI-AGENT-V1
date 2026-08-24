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
