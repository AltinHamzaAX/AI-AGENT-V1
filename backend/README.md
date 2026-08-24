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
