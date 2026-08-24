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
