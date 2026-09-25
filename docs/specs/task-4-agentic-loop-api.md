# Task 4 — Self-correcting LangGraph loop, synthesis, triad eval, chat API

**Purpose.** LangGraph graph: retrieve → evaluate sufficiency (structured Pydantic verdict listing
missing identifiers) → re-query for missing identifiers (bounded loop) → synthesize or control-gate
"insufficient data" fallback. Synthesis prompt keeps retrieved content in a delimited data block,
never in instructions (prompt-injection defense). Ragas RAG-triad (context relevance, groundedness,
answer relevance) computed and logged per answer. Tracing via Langfuse when keys exist, else
structured-log spans. FastAPI `/chat` and `/ingest` with session-bound tenant auth; 4xx vs 5xx.

**Verification.** Unit: graph routing with a labeled test-double LLM (sufficient / re-query /
fallback paths); injection text in a chunk stays inside the data block. Rung 2: real LLM call if a
credential exists, else UNEXECUTED and logged in `known_limitations.md`.
