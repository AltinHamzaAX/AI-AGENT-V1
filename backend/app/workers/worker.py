import asyncio
import logging
import signal

from app.core.logging import configure_logging
from app.infrastructure.health import check_dependencies

logger = logging.getLogger(__name__)


async def run() -> None:
    configure_logging()
    services = await check_dependencies()
    if not all(value == "ok" for value in services.values()):
        raise RuntimeError(f"Worker dependencies unavailable: {services}")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signame, stop_event.set)

    logger.info(
        "Promotiva durable generation queue ready; "
        "Post Supervisor execution awaits Ticket 11 wiring"
    )
    await stop_event.wait()


if __name__ == "__main__":
    asyncio.run(run())
