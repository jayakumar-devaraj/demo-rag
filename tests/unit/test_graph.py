"""Unit tests for the self-correcting LangGraph loop (test-double LLM and retriever, no DB)."""

from src.agents.synthesis import INSUFFICIENT_MESSAGE, Answer, SufficiencyVerdict
from src.db.connection import run
from src.orchestration.graph import MAX_ATTEMPTS, build_graph
from src.retrieval.hybrid import ParentSection
from tests.conftest import ScriptedLLM


def _section(pid: int, text: str) -> ParentSection:
    return ParentSection(page_id=pid, doc_key="d", page_key=f"page:{pid}", source_name="f.pdf",
                         markdown=text, score=1.0, chunk_ids=[pid])


CORPUS = {"total budget": [_section(1, "Budget for PRJ-7 see appendix")], "PRJ-7": [_section(2, "| PRJ-7 | 4200 |")]}


def _retriever(calls: list[str]):
    async def retriever(q: str) -> list[ParentSection]:
        calls.append(q)
        return CORPUS.get(q, [])
    return retriever


def _ask(llm, calls):
    graph = build_graph(_retriever(calls), llm)
    return run(graph.ainvoke({"question": "total budget", "pending_queries": ["total budget"]}))


def test_sufficient_first_pass_synthesizes():
    llm = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)],
                      Answer=[Answer(answer="See appendix.", citations=["d#page:1"])])
    calls: list[str] = []
    out = _ask(llm, calls)
    assert out["answer"].answer == "See appendix." and out["attempts"] == 1 and calls == ["total budget"]


def test_missing_identifier_triggers_requery_and_appends_context():
    llm = ScriptedLLM(
        SufficiencyVerdict=[SufficiencyVerdict(sufficient=False, missing_identifiers=["PRJ-7"]),
                            SufficiencyVerdict(sufficient=True)],
        Answer=[Answer(answer="PRJ-7 budget is 4200.", citations=["d#page:2"])])
    calls: list[str] = []
    out = _ask(llm, calls)
    assert calls == ["total budget", "PRJ-7"]
    assert [s.page_id for s in out["sections"]] == [1, 2]  # new matrix appended, original kept
    assert out["answer"].answer == "PRJ-7 budget is 4200." and out["attempts"] == 2
    assert "| PRJ-7 | 4200 |" in llm.prompts[-1][1].content  # synthesis saw the appended context


def test_still_insufficient_after_loop_hits_control_gate():
    verdicts = [SufficiencyVerdict(sufficient=False, missing_identifiers=[f"X-{i}"]) for i in range(MAX_ATTEMPTS)]
    llm = ScriptedLLM(SufficiencyVerdict=verdicts, Answer=[])
    out = _ask(llm, [])
    assert out["answer"].insufficient and out["answer"].answer == INSUFFICIENT_MESSAGE
    assert out["attempts"] == MAX_ATTEMPTS


def test_hallucinated_number_is_blocked():
    llm = ScriptedLLM(SufficiencyVerdict=[SufficiencyVerdict(sufficient=True)],
                      Answer=[Answer(answer="The budget is 9999.")])
    out = _ask(llm, [])
    assert out["answer"].insufficient and out["answer"].answer == INSUFFICIENT_MESSAGE
