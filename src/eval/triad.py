"""RAG-triad evaluation: Ragas LLM metrics plus a deterministic numeric-groundedness check."""

import asyncio
import logging
import re
import sys
import types
from typing import Any, Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)

TRIAD_TIMEOUT_S = 90
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


class TriadScores(BaseModel):
    """Scores logged for every answer. Ragas fields are None when no evaluator LLM is available."""

    context_relevance: float | None = None
    groundedness: float | None = None
    answer_relevance: float | None = None
    numeric_groundedness: float
    unsupported_numbers: list[str]


class TriadScorer(Protocol):
    """Computes TriadScores for one answer."""

    async def __call__(self, question: str, answer: str, contexts: list[str]) -> TriadScores:
        """Score one question/answer/context triple."""
        ...


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def numeric_groundedness(answer: str, contexts: list[str]) -> tuple[float, list[str]]:
    """Fraction of numbers in the answer that literally occur in the context, plus the unsupported ones."""
    claimed = _numbers(answer)
    if not claimed:
        return 1.0, []
    available = set().union(*(_numbers(c) for c in contexts)) if contexts else set()
    unsupported = sorted(claimed - available)
    return 1 - len(unsupported) / len(claimed), unsupported


def _import_ragas() -> Any:
    """Import Ragas metrics.

    Ragas 0.4.3 imports langchain_community.chat_models.vertexai at module load, which
    langchain-community 0.4.x (the line compatible with langchain-core 1.x) no longer ships.
    Ragas never uses it on this path, so an empty placeholder module is registered first.
    """
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        shim = types.ModuleType(name)
        shim.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules[name] = shim
    from ragas import metrics  # noqa: PLC0415 - deferred until the shim is in place

    return metrics


class RagasTriadScorer:
    """Ragas-backed triad: ContextRelevance, Faithfulness (groundedness), ResponseRelevancy."""

    def __init__(self, llm: Any, embeddings: Any) -> None:
        """Wrap a LangChain chat model and LangChain embeddings for Ragas."""
        from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: PLC0415
        from ragas.llms import LangchainLLMWrapper  # noqa: PLC0415

        m = _import_ragas()
        # VERIFY: Ragas 0.4.3 metric constructors / single_turn_ascore against a live LLM (no credentials here).
        rllm, remb = LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(embeddings)
        self._metrics = {
            "context_relevance": m.ContextRelevance(llm=rllm),
            "groundedness": m.Faithfulness(llm=rllm),
            "answer_relevance": m.ResponseRelevancy(llm=rllm, embeddings=remb),
        }

    async def __call__(self, question: str, answer: str, contexts: list[str]) -> TriadScores:
        """Score all three metrics concurrently; a metric that errors is logged and left None."""
        from ragas.dataset_schema import SingleTurnSample  # noqa: PLC0415

        sample = SingleTurnSample(user_input=question, response=answer, retrieved_contexts=contexts)
        names = list(self._metrics)
        results = await asyncio.wait_for(
            asyncio.gather(*(self._metrics[n].single_turn_ascore(sample) for n in names), return_exceptions=True),
            TRIAD_TIMEOUT_S,
        )
        scores: dict[str, float | None] = {}
        for n, r in zip(names, results, strict=True):
            if isinstance(r, Exception):
                log.warning("triad_metric_failed", extra={"metric": n, "error": type(r).__name__})
                scores[n] = None
            else:
                scores[n] = float(r)
        ratio, unsupported = numeric_groundedness(answer, contexts)
        return TriadScores(**scores, numeric_groundedness=ratio, unsupported_numbers=unsupported)
