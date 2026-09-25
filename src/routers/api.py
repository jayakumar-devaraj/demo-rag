"""HTTP API: /ingest and /chat, tenant bound to the bearer token, 4xx vs 5xx kept distinct."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.db.connection import apply_schema, connect
from src.eval.triad import TriadScorer, TriadScores, numeric_groundedness
from src.ingestion.pipeline import Embedder, IngestResult, ingest_file
from src.ingestion.workers import UnsupportedFormatError, worker_for
from src.orchestration.graph import build_graph
from src.retrieval.hybrid import ParentSection, retrieve
from src.utils.auth import AuthError, Session, authenticate
from src.utils.observability import trace_callbacks

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@dataclass
class Services:
    """Runtime dependencies; None means that provider is not configured (→ 503)."""

    embedder: Embedder | None
    llm: Any | None
    triad: TriadScorer | None


class ChatRequest(BaseModel):
    """Chat input; deliberately has no tenant field."""

    question: str = Field(min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    """Answer plus the evidence and evaluation logged for it."""

    answer: str
    insufficient: bool
    citations: list[str]
    queries_run: list[str]
    attempts: int
    triad: TriadScores


_bearer = HTTPBearer(auto_error=False)
router = APIRouter()


def get_session(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> Session:
    """Authenticate the bearer token; the Session's tenant is the only tenant any query sees."""
    try:
        return authenticate(creds.credentials if creds else None)
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


def _services(request: Request) -> Services:
    return request.app.state.services


async def _connect() -> psycopg.AsyncConnection:
    try:
        return await connect()
    except psycopg.OperationalError as exc:
        log.error("db_unavailable", extra={"error": type(exc).__name__})
        raise HTTPException(503, "database unavailable") from exc


@router.post("/ingest", response_model=IngestResult)
async def ingest(file: UploadFile, session: Annotated[Session, Depends(get_session)],
                 services: Annotated[Services, Depends(_services)]) -> IngestResult:
    """Ingest one document into the caller's tenant."""
    if services.embedder is None:
        raise HTTPException(503, "no embedding provider configured")
    try:
        worker_for(file.filename or "")
    except UnsupportedFormatError as exc:
        raise HTTPException(415, str(exc)) from exc
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    conn = await _connect()
    try:
        return await ingest_file(conn, services.embedder, session.tenant_id, file.filename or "upload", data)
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    except (ValueError, KeyError) as exc:  # unparseable file content from the caller
        raise HTTPException(422, f"could not parse file: {type(exc).__name__}") from exc
    finally:
        await conn.close()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, session: Annotated[Session, Depends(get_session)],
               services: Annotated[Services, Depends(_services)]) -> ChatResponse:
    """Answer a question from the caller's tenant only, via the self-correcting graph."""
    if services.llm is None or services.embedder is None:
        raise HTTPException(503, "no LLM/embedding provider configured")
    conn = await _connect()
    try:
        async def bound_retriever(query: str) -> list[ParentSection]:
            return await retrieve(conn, services.embedder, session, query)

        graph = build_graph(bound_retriever, services.llm)
        state = await graph.ainvoke({"question": body.question, "pending_queries": [body.question]},
                                    config={"callbacks": trace_callbacks()})
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    except TimeoutError as exc:
        raise HTTPException(504, "model call timed out") from exc
    finally:
        await conn.close()
    answer, contexts = state["answer"], [s.markdown for s in state["sections"]]
    if services.triad is not None:
        triad = await services.triad(body.question, answer.answer, contexts)
    else:
        ratio, unsupported = numeric_groundedness(answer.answer, contexts)
        triad = TriadScores(numeric_groundedness=ratio, unsupported_numbers=unsupported)
    log.info("chat_answered", extra={"tenant_id": session.tenant_id, "attempts": state["attempts"],
                                     "insufficient": answer.insufficient, "triad": triad.model_dump()})
    return ChatResponse(answer=answer.answer, insufficient=answer.insufficient, citations=answer.citations,
                        queries_run=state["queries_run"], attempts=state["attempts"], triad=triad)


@router.get("/health")
async def health(services: Annotated[Services, Depends(_services)]) -> dict[str, bool]:
    """Report which providers are configured."""
    return {"embedder": services.embedder is not None, "llm": services.llm is not None}


def create_app(services: Services) -> FastAPI:
    """Build the FastAPI app around injected services."""
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Provision the idempotent schema at startup; an unreachable database fails startup loudly."""
        conn = await connect()
        try:
            await apply_schema(conn)
        finally:
            await conn.close()
        log.info("schema_applied")
        yield

    app = FastAPI(title="demo-rag", lifespan=lifespan)
    app.state.services = services
    app.include_router(router)
    return app
