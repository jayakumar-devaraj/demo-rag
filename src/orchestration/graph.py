"""Self-correcting loop: retrieve → evaluate → (re-query → retrieve)* → synthesize → guard | fallback."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agents.synthesis import (
    INSUFFICIENT_MESSAGE,
    Answer,
    StructuredLLM,
    SufficiencyVerdict,
    evaluate_sufficiency,
    synthesize,
)
from src.eval.triad import numeric_groundedness
from src.orchestration.state import RagState
from src.retrieval.hybrid import ParentSection

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # the initial retrieval plus up to two corrective re-query passes

Retriever = Callable[[str], Awaitable[list[ParentSection]]]


def _timed(name: str, fn: Callable[[RagState], Awaitable[dict]]) -> Callable[[RagState], Awaitable[dict]]:
    """Wrap a node with a structured-log span (name, duration)."""

    async def node(state: RagState) -> dict:
        start = time.perf_counter()
        out = await fn(state)
        log.info("node", extra={"node": name, "ms": round((time.perf_counter() - start) * 1000, 1)})
        return out

    return node


def build_graph(retriever: Retriever, llm: StructuredLLM, *, retrieve_only: bool = False) -> Any:
    """Compile the graph. `retriever` is already bound to the caller's authenticated Session.

    With `retrieve_only`, sufficient context ends the run at END instead of synthesizing, so the caller
    writes the answer itself; the retrieve → evaluate → re-query loop is the same code either way.
    """

    async def retrieve(state: RagState) -> dict:
        sections = {s.page_id: s for s in state.get("sections", [])}
        for q in state["pending_queries"]:
            for s in await retriever(q):
                sections.setdefault(s.page_id, s)  # new parent context is appended, never replaced
        return {"sections": list(sections.values()), "attempts": state.get("attempts", 0) + 1,
                "queries_run": state.get("queries_run", []) + state["pending_queries"], "pending_queries": []}

    async def evaluate(state: RagState) -> dict:
        if not state["sections"]:
            return {"verdict": SufficiencyVerdict(sufficient=False, reason="no context retrieved")}
        return {"verdict": await evaluate_sufficiency(llm, state["question"], state["sections"])}

    def route(state: RagState) -> str:
        if state["verdict"].sufficient:
            return END if retrieve_only else "synthesize"
        fresh = [m for m in state["verdict"].missing_identifiers if m not in state["queries_run"]]
        return "requery" if fresh and state["attempts"] < MAX_ATTEMPTS else "fallback"

    async def requery(state: RagState) -> dict:
        missing = state["verdict"].missing_identifiers
        return {"pending_queries": [m for m in missing if m not in state["queries_run"]]}

    async def generate(state: RagState) -> dict:
        answer = await synthesize(llm, state["question"], state["sections"])
        _, unsupported = numeric_groundedness(answer.answer, [s.markdown for s in state["sections"]])
        if answer.insufficient or unsupported:
            log.warning("control_gate", extra={"insufficient": answer.insufficient, "unsupported": unsupported})
            return {"answer": Answer(answer=INSUFFICIENT_MESSAGE, insufficient=True)}
        return {"answer": answer}

    async def fallback(state: RagState) -> dict:
        return {"answer": Answer(answer=INSUFFICIENT_MESSAGE, insufficient=True)}

    g = StateGraph(RagState)
    for name, fn in [("retrieve", retrieve), ("evaluate", evaluate), ("requery", requery),
                     ("synthesize", generate), ("fallback", fallback)]:
        g.add_node(name, _timed(name, fn))
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "evaluate")
    g.add_conditional_edges("evaluate", route, ["synthesize", "requery", "fallback", END])
    g.add_edge("requery", "retrieve")
    g.add_edge("synthesize", END)
    g.add_edge("fallback", END)
    return g.compile()
