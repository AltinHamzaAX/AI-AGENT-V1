import asyncio
import logging
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.infrastructure.database.repositories.execution_traces import (
    SQLAlchemyExecutionTraceRecorder,
)
from app.infrastructure.database.session import AsyncSessionFactory, close_database
from app.infrastructure.health import check_dependencies
from app.integrations.provider_factory import create_provider_bundle
from app.workers.generation_worker import GenerationWorker
from app.workers.pipeline import build_generation_executor

logger = logging.getLogger(__name__)


async def run() -> None:
    configure_logging()
    services = await check_dependencies()
    if not all(value == "ok" for value in services.values()):
        raise RuntimeError(f"Worker dependencies unavailable: {services}")

    settings = get_settings()
    trace_recorder = SQLAlchemyExecutionTraceRecorder(AsyncSessionFactory)
    worker = GenerationWorker(
        session_factory=AsyncSessionFactory,
        executor=build_generation_executor(
            AsyncSessionFactory,
            create_provider_bundle(settings),
            settings=settings,
            trace_recorder=trace_recorder,
        ),
        lease_seconds=settings.generation_job_lease_seconds,
        retry_backoff_seconds=settings.generation_job_retry_backoff_seconds,
        poll_seconds=settings.generation_worker_poll_seconds,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, stop_event.set)

    logger.info(
        "posts.worker.started",
        extra={
            "event": "posts.worker.started",
            "worker_id": worker.worker_id,
            "poll_seconds": settings.generation_worker_poll_seconds,
        },
    )
    try:
        # The loop claims one job at a time; a stop signal is honoured between
        # jobs so a running generation keeps its lease instead of being torn
        # down halfway through a stage.
        await worker.run_forever(stop_event=stop_event)
    finally:
        logger.info("posts.worker.stopped", extra={"event": "posts.worker.stopped"})
        await close_database()


if __name__ == "__main__":
    asyncio.run(run())
