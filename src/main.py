"""Single runnable entrypoint: `uv run python -m src.main`."""

import asyncio
import logging
import os
from typing import Any

import uvicorn
from pydantic import BaseModel

from src.routers.api import Services, create_app
from src.utils.observability import configure_logging

log = logging.getLogger(__name__)

CHAT_TIMEOUT_S = 60


class _LangchainEmbedder:
    """Adapts a LangChain Embeddings object to the pipeline's async Embedder protocol."""

    def __init__(self, embeddings: Any) -> None:
        self._e = embeddings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts asynchronously."""
        return await self._e.aembed_documents(texts)


class _NamedEmbeddings:
    """Delegating embeddings whose `model` is the model *name*: Ragas usage tracking requires a str there,
    while LangChain's FastEmbedEmbeddings stores the fastembed model object in that attribute."""

    def __init__(self, inner: Any, name: str) -> None:
        self._inner, self.model = inner, name

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents."""
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """Embed one query."""
        return self._inner.embed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents asynchronously."""
        return await self._inner.aembed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        """Embed one query asynchronously."""
        return await self._inner.aembed_query(text)


class FallbackLLM:
    """A primary chat model with an ordered fallback: each structured call tries primary, then fallback."""

    def __init__(self, primary: Any, fallback: Any | None) -> None:
        self.primary, self.fallback = primary, fallback

    def with_structured_output(self, schema: type[BaseModel]) -> Any:
        """Return a runnable that validates into `schema`, falling back on any primary failure."""
        runnable = self.primary.with_structured_output(schema)
        return runnable.with_fallbacks([self.fallback.with_structured_output(schema)]) if self.fallback else runnable


def _chat_models(max_retries: int = 2) -> list[Any]:
    """Configured chat models in priority order: Groq first, OpenAI as fallback."""
    models = []
    if os.environ.get("GROQ_API_KEY") and os.environ.get("GROQ_CHAT_MODEL"):
        from langchain_groq import ChatGroq  # noqa: PLC0415

        models.append(ChatGroq(model=os.environ["GROQ_CHAT_MODEL"], timeout=CHAT_TIMEOUT_S, max_retries=max_retries))
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_CHAT_MODEL"):
        from langchain_openai import ChatOpenAI  # noqa: PLC0415

        models.append(ChatOpenAI(model=os.environ["OPENAI_CHAT_MODEL"], timeout=CHAT_TIMEOUT_S, max_retries=max_retries))
    return models


def _embeddings() -> Any | None:
    """Local fastembed when FASTEMBED_MODEL is set, else OpenAI embeddings; the dimension must match the schema."""
    from src.db.connection import EMBEDDING_DIM  # noqa: PLC0415

    if os.environ.get("FASTEMBED_MODEL"):
        from langchain_community.embeddings.fastembed import FastEmbedEmbeddings  # noqa: PLC0415

        embeddings = FastEmbedEmbeddings(model_name=os.environ["FASTEMBED_MODEL"])
        dim = len(embeddings.embed_query("dimension check"))
        if dim != EMBEDDING_DIM:
            raise RuntimeError(f"FASTEMBED_MODEL yields {dim}-dim vectors; schema requires {EMBEDDING_DIM}")
        return embeddings
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_EMBEDDING_MODEL"):
        from langchain_openai import OpenAIEmbeddings  # noqa: PLC0415

        return OpenAIEmbeddings(model=os.environ["OPENAI_EMBEDDING_MODEL"], dimensions=EMBEDDING_DIM)
    return None


def build_services() -> Services:
    """Build providers from the environment; any provider left unconfigured stays None (endpoints → 503).

    Model names are required settings, never defaults, so no model id is assumed here.
    """
    from src.eval.triad import RagasTriadScorer  # noqa: PLC0415

    chats, embeddings = _chat_models(), _embeddings()
    if not chats or embeddings is None:
        log.warning("providers_unconfigured", extra={"chat": bool(chats), "embeddings": embeddings is not None,
                                                     "needs": "GROQ_* or OPENAI_* chat; FASTEMBED_MODEL or OPENAI_* embeddings"})
    llm = FallbackLLM(chats[0], chats[1] if len(chats) > 1 else None) if chats else None
    # /v1/retrieve callers own the retry policy, so its models retry inside the provider client far less than /chat's
    lean = _chat_models(int(os.environ.get("RETRIEVE_LLM_MAX_RETRIES", "0")))
    retrieve_llm = FallbackLLM(lean[0], lean[1] if len(lean) > 1 else None) if lean else None
    named = None
    if embeddings is not None:
        name = getattr(embeddings, "model_name", None) or getattr(embeddings, "model", "")
        named = _NamedEmbeddings(embeddings, str(name))
    triad = RagasTriadScorer(chats[0], named) if chats and named is not None else None
    return Services(embedder=_LangchainEmbedder(embeddings) if embeddings is not None else None, llm=llm, triad=triad,
                    retrieve_llm=retrieve_llm)


def main() -> None:
    """Serve the API on a SelectorEventLoop (psycopg async cannot run on Windows' Proactor loop)."""
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    app = create_app(build_services())
    config = uvicorn.Config(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
