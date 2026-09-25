"""Single runnable entrypoint: `uv run python -m src.main`."""

import asyncio
import logging
import os

import uvicorn

from src.routers.api import Services, create_app
from src.utils.observability import configure_logging

log = logging.getLogger(__name__)


class _OpenAIEmbedder:
    """Adapts LangChain OpenAIEmbeddings to the pipeline's async Embedder protocol."""

    def __init__(self, embeddings: object) -> None:
        self._e = embeddings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts asynchronously."""
        return await self._e.aembed_documents(texts)


def build_services() -> Services:
    """Build providers from the environment; any provider left unconfigured stays None (endpoints → 503).

    Model names are required settings, never defaults, so no model id is assumed here.
    """
    if not (os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_CHAT_MODEL")
            and os.environ.get("OPENAI_EMBEDDING_MODEL")):
        log.warning("providers_unconfigured", extra={"needs": "OPENAI_API_KEY, OPENAI_CHAT_MODEL, OPENAI_EMBEDDING_MODEL"})
        return Services(embedder=None, llm=None, triad=None)
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # noqa: PLC0415

    from src.db.connection import EMBEDDING_DIM  # noqa: PLC0415
    from src.eval.triad import RagasTriadScorer  # noqa: PLC0415

    # VERIFY: live OpenAI calls (no credentials available during the build).
    llm = ChatOpenAI(model=os.environ["OPENAI_CHAT_MODEL"], timeout=60, max_retries=2)
    embeddings = OpenAIEmbeddings(model=os.environ["OPENAI_EMBEDDING_MODEL"], dimensions=EMBEDDING_DIM)
    return Services(embedder=_OpenAIEmbedder(embeddings), llm=llm, triad=RagasTriadScorer(llm, embeddings))


def main() -> None:
    """Serve the API on a SelectorEventLoop (psycopg async cannot run on Windows' Proactor loop)."""
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    app = create_app(build_services())
    config = uvicorn.Config(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
