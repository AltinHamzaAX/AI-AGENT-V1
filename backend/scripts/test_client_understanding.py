"""Run the Client Understanding Agent manually against the configured LLM."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow this file to be run directly from backend/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.integrations.provider_factory import create_llm_provider  # noqa: E402
from app.modules.posts.agents.client_understanding import (  # noqa: E402
    CLIENT_UNDERSTANDING_AGENT_NAME,
    ClientUnderstandingInput,
    register_client_understanding_agent,
)
from app.modules.posts.agents.framework import AgentRuntime  # noqa: E402
from app.modules.posts.tools import ToolRegistry  # noqa: E402

DEFAULT_MESSAGE = (
    "Kjo eshte kafiteria ime ne Prishtine. Brandi quhet LUMMA. Dua nje post "
    "ne Instagram, ne gjuhen shqipe, per me shume vizita."
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test the Ticket 13 Client Understanding Agent with the configured LLM."
    )
    parser.add_argument(
        "message",
        nargs="?",
        default=DEFAULT_MESSAGE,
        help="Latest client message to analyze.",
    )
    return parser.parse_args()


async def _run(message: str) -> None:
    settings = get_settings()
    running_in_container = Path("/.dockerenv").exists()
    if (
        not running_in_container
        and settings.ollama_base_url.rstrip("/") == "http://host.docker.internal:11434"
    ):
        settings = settings.model_copy(update={"ollama_base_url": "http://localhost:11434"})

    runtime = AgentRuntime(ToolRegistry())
    register_client_understanding_agent(runtime, create_llm_provider(settings))
    result = await runtime.run(
        CLIENT_UNDERSTANDING_AGENT_NAME,
        ClientUnderstandingInput(
            latest_message=message,
        ),
    )

    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


def main() -> None:
    arguments = _arguments()
    asyncio.run(_run(arguments.message))


if __name__ == "__main__":
    main()
