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
queues, and object storage. `app/integrations` defines external AI and research
provider boundaries. Provider interfaces keep vendor-specific clients out of
business modules.

## Runtime responsibilities

- `frontend`: Nuxt user interface.
- `api`: FastAPI HTTP process.
- `worker`: future background jobs and long-running AI workflows.
- `db`: PostgreSQL 17 with pgvector.
- `redis`: queue, cache, and transient coordination.
- `minio`: local S3-compatible object storage.
- `minio-init`: idempotent local bucket initialization.

No business database models, workflows, agents, or provider adapters are included
in this foundation.
