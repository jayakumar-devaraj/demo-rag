"""LangGraph state for the self-correcting RAG loop."""

from typing import TypedDict

from src.agents.synthesis import Answer, SufficiencyVerdict
from src.retrieval.hybrid import ParentSection


class RagState(TypedDict, total=False):
    """Graph state. The tenant is not in state: the retriever is bound to the Session."""

    question: str
    pending_queries: list[str]
    queries_run: list[str]
    sections: list[ParentSection]
    attempts: int
    verdict: SufficiencyVerdict
    answer: Answer
