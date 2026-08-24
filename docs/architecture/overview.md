# Architecture overview

Promotiva is a modular monolith: one FastAPI application and one shared worker
codebase, split into explicit business modules. It is not a collection of
microservices.

## Dependency direction

```text
HTTP routes -> application services -> module orchestration -> domain contracts
                                                        -> infrastructure adapters
                                                        -> provider integrations
```

Routes translate HTTP requests and responses. They do not query databases or run
workflows. `app/main.py` only constructs the application. Cross-cutting startup and
shutdown behavior belongs in `core/lifecycle.py`.

`app/infrastructure` contains technical implementations for PostgreSQL, Redis,
queues, and object storage. Persistence contracts stay outside infrastructure;
SQLAlchemy repositories and Unit of Work adapters implement those contracts.
`app/integrations` defines external AI and research provider boundaries. Provider
interfaces keep vendor-specific clients out of business modules.

## Runtime responsibilities

- `frontend`: Nuxt user interface.
- `api`: FastAPI HTTP process.
- `worker`: future background jobs and long-running AI workflows.
- `db`: PostgreSQL 17 with pgvector.
- `redis`: queue, cache, and transient coordination.
- `minio`: local S3-compatible object storage.
- `minio-init`: idempotent local bucket initialization.

Schema changes and database extensions are versioned with Alembic. The current
business schema stores scoped conversations, messages, message-linked asset
metadata, Posts, generation attempts, and artifact references. Binary assets
live behind an application-owned storage port and use
the configured private S3-compatible bucket; MinIO is only the local adapter.
AI workflows and agents remain future work.
