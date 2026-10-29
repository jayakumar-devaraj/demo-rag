"""HTTP API: /ingest, /chat and /v1/retrieve, tenant bound to the bearer token, 4xx vs 5xx kept distinct."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import groq
import openai
import psycopg
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from src.db.audit import Action, AuditEntry, list_audit, record_audit
from src.db.connection import connect, require_schema_revision
from src.eval.triad import TriadScorer, TriadScores, numeric_groundedness
from src.ingestion.pipeline import Embedder, IngestResult, ingest_file
from src.ingestion.workers import UnsupportedFormatError, worker_for
from src.orchestration.graph import build_graph
from src.retrieval.hybrid import ParentSection, retrieve
from src.utils.auth import (
    SCOPE_INGEST,
    SCOPE_QUERY,
    AuthError,
    IdentityProviderUnavailable,
    Session,
    authenticate,
)
from src.utils.observability import (
    install_request_id_logging,
    request_id,
    resolve_request_id,
    resolve_traceparent,
    trace_callbacks,
    traceparent,
)

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PROVIDER_ERRORS = (openai.APIError, groq.APIError)
PROVIDER_TIMEOUTS = (openai.APITimeoutError, groq.APITimeoutError)
RETRIEVE_DEFAULT_TIMEOUT_S = 30.0
RETRIEVE_MAX_TIMEOUT_MS = 120_000


@dataclass
class Services:
    """Runtime dependencies; None means that provider is not configured (→ 503).

    `retrieve_llm` is the chat model for /v1/retrieve (provider-level retries lowered); None reuses `llm`.
    """

    embedder: Embedder | None
    llm: Any | None
    triad: TriadScorer | None
    retrieve_llm: Any | None = None


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


class RetrieveRequest(BaseModel):
    """Retrieval input; deliberately has no tenant field."""

    question: str = Field(min_length=1, max_length=2000)


class RetrieveResponse(BaseModel):
    """Parent sections the caller may answer from. `sections` is empty whenever `insufficient` is true."""

    sections: list[ParentSection]
    insufficient: bool
    queries_run: list[str]
    attempts: int


class AuditPage(BaseModel):
    """One page of the caller's tenant audit trail, newest first; pass `next_before_id` back to continue."""

    entries: list[AuditEntry]
    next_before_id: int | None


class ErrorBody(BaseModel):
    """Body of every handled error response."""

    detail: str


_bearer = HTTPBearer(auto_error=False)
router = APIRouter()
v1 = APIRouter(prefix="/v1")


def get_session(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> Session:
    """Authenticate the bearer token; the Session's tenant is the only tenant any query sees."""
    try:
        return authenticate(creds.credentials if creds else None)
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    except IdentityProviderUnavailable as exc:
        raise HTTPException(503, "identity provider unavailable") from exc


def require_scope(scope: str) -> Any:
    """A dependency that authenticates, then requires `scope` (403 when the token lacks it)."""

    def dependency(session: Annotated[Session, Depends(get_session)]) -> Session:
        if scope not in session.scopes:
            raise HTTPException(403, f"token lacks the {scope} scope")
        return session

    return dependency


query_session = require_scope(SCOPE_QUERY)
ingest_session = require_scope(SCOPE_INGEST)


def _provider_down(exc: Exception) -> HTTPException:
    """Model-provider failure (quota, outage, auth) is an infrastructure error, not the caller's."""
    log.error("provider_error", extra={"error": type(exc).__name__, "status": getattr(exc, "status_code", None)})
    return HTTPException(503, "model provider unavailable")


def _services(request: Request) -> Services:
    return request.app.state.services


async def _connect(tenant_id: str) -> psycopg.AsyncConnection:
    try:
        return await connect(tenant_id)
    except psycopg.OperationalError as exc:
        log.error("db_unavailable", extra={"error": type(exc).__name__})
        raise HTTPException(503, "database unavailable") from exc


async def _audit_failure(conn: psycopg.AsyncConnection, session: Session, action: Action, exc: Exception) -> None:
    """Record that an authenticated request failed; this must never mask the failure being reported."""
    try:
        await record_audit(conn, session, action, "error", detail={"error": type(exc).__name__})
    except psycopg.Error:
        log.error("audit_error_not_recorded", extra={"action": action, "tenant_id": session.tenant_id})


@router.post("/ingest", response_model=IngestResult)
async def ingest(file: UploadFile, session: Annotated[Session, Depends(ingest_session)],
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
    conn = await _connect(session.tenant_id)
    filename = file.filename or "upload"
    try:
        # One transaction: the document's pages and its audit row commit or roll back together.
        async with conn.transaction():
            result = await ingest_file(conn, services.embedder, session.tenant_id, filename, data)
            await record_audit(conn, session, "ingest", "ok", doc_key=result.doc_key,
                               detail={"filename": filename, "units": len(result.units),
                                       "purged_pages": result.purged_pages})
        return result
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    except PROVIDER_ERRORS as exc:
        await _audit_failure(conn, session, "ingest", exc)
        raise _provider_down(exc) from exc
    except (ValueError, KeyError) as exc:  # unparseable file content from the caller
        await _audit_failure(conn, session, "ingest", exc)
        raise HTTPException(422, f"could not parse file: {type(exc).__name__}") from exc
    finally:
        await conn.close()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, session: Annotated[Session, Depends(query_session)],
               services: Annotated[Services, Depends(_services)]) -> ChatResponse:
    """Answer a question from the caller's tenant only, via the self-correcting graph."""
    if services.llm is None or services.embedder is None:
        raise HTTPException(503, "no LLM/embedding provider configured")
    conn = await _connect(session.tenant_id)
    try:
        async def bound_retriever(query: str) -> list[ParentSection]:
            return await retrieve(conn, services.embedder, session, query)

        graph = build_graph(bound_retriever, services.llm)
        state = await graph.ainvoke({"question": body.question, "pending_queries": [body.question]},
                                    config={"callbacks": trace_callbacks()})
        # No audit row, no answer: a failed insert here fails the request instead of releasing unrecorded data.
        await record_audit(conn, session, "chat", "ok", detail={
            "attempts": state["attempts"], "queries": len(state["queries_run"]),
            "sections": len(state["sections"]), "insufficient": state["answer"].insufficient})
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    except TimeoutError as exc:
        await _audit_failure(conn, session, "chat", exc)
        raise HTTPException(504, "model call timed out") from exc
    except PROVIDER_ERRORS as exc:
        await _audit_failure(conn, session, "chat", exc)
        raise _provider_down(exc) from exc
    finally:
        await conn.close()
    answer, contexts = state["answer"], [s.markdown for s in state["sections"]]
    ratio, unsupported = numeric_groundedness(answer.answer, contexts)
    triad = TriadScores(numeric_groundedness=ratio, unsupported_numbers=unsupported)
    if services.triad is not None:
        try:
            triad = await services.triad(body.question, answer.answer, contexts)
        except (TimeoutError, *PROVIDER_ERRORS) as exc:  # evaluation must not withhold a validated answer
            log.warning("triad_unavailable", extra={"error": type(exc).__name__})
    log.info("chat_answered", extra={"tenant_id": session.tenant_id, "attempts": state["attempts"],
                                     "insufficient": answer.insufficient, "triad": triad.model_dump()})
    return ChatResponse(answer=answer.answer, insufficient=answer.insufficient, citations=answer.citations,
                        queries_run=state["queries_run"], attempts=state["attempts"], triad=triad)


@v1.post("/retrieve", response_model=RetrieveResponse, operation_id="retrieve", responses={
    401: {"model": ErrorBody, "description": "Missing or unknown bearer token."},
    503: {"model": ErrorBody, "description": "Model provider or database unavailable, or no provider configured."},
    504: {"model": ErrorBody, "description": "Model call timed out, or the caller's deadline elapsed."},
    500: {"description": "Unexpected server error (plain-text body)."},
})
async def retrieve_context(
    body: RetrieveRequest, session: Annotated[Session, Depends(query_session)],
    services: Annotated[Services, Depends(_services)],
    x_timeout_ms: Annotated[int | None, Header(ge=1, le=RETRIEVE_MAX_TIMEOUT_MS, description=(
        "Caller's deadline in milliseconds for the whole call. Absent: the server default applies."))] = None,
) -> RetrieveResponse:
    """Run the self-correcting retrieval loop for the caller's tenant and return passages; no answer is written."""
    llm = services.retrieve_llm or services.llm
    if llm is None or services.embedder is None:
        raise HTTPException(503, "no LLM/embedding provider configured")
    timeout_s = x_timeout_ms / 1000 if x_timeout_ms else RETRIEVE_DEFAULT_TIMEOUT_S
    conn = await _connect(session.tenant_id)
    try:
        async def bound_retriever(query: str) -> list[ParentSection]:
            return await retrieve(conn, services.embedder, session, query)

        graph = build_graph(bound_retriever, llm, retrieve_only=True)
        async with asyncio.timeout(timeout_s):
            state = await graph.ainvoke({"question": body.question, "pending_queries": [body.question]},
                                        config={"callbacks": trace_callbacks()})
        insufficient = not state["verdict"].sufficient
        await record_audit(conn, session, "retrieve", "ok", detail={
            "attempts": state["attempts"], "queries": len(state["queries_run"]),
            "sections": 0 if insufficient else len(state["sections"]), "insufficient": insufficient})
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    except (TimeoutError, *PROVIDER_TIMEOUTS) as exc:
        await _audit_failure(conn, session, "retrieve", exc)
        raise HTTPException(504, "model call timed out") from exc
    except PROVIDER_ERRORS as exc:
        await _audit_failure(conn, session, "retrieve", exc)
        raise _provider_down(exc) from exc
    finally:
        await conn.close()
    log.info("retrieve_answered", extra={"tenant_id": session.tenant_id, "attempts": state["attempts"],
                                         "insufficient": insufficient, "sections": len(state["sections"])})
    return RetrieveResponse(sections=[] if insufficient else state["sections"], insufficient=insufficient,
                            queries_run=state["queries_run"], attempts=state["attempts"])


@router.get("/audit", response_model=AuditPage)
async def audit_trail(session: Annotated[Session, Depends(query_session)],
                      limit: Annotated[int, Query(ge=1, le=200)] = 50,
                      before_id: Annotated[int | None, Query(ge=1)] = None,
                      action: Action | None = None) -> AuditPage:
    """The caller's own tenant audit trail, newest first. The tenant comes only from the token."""
    conn = await _connect(session.tenant_id)
    try:
        entries = await list_audit(conn, session.tenant_id, limit, before_id, action)
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    finally:
        await conn.close()
    return AuditPage(entries=entries, next_before_id=entries[-1].id if len(entries) == limit else None)


@router.get("/health")
async def health(services: Annotated[Services, Depends(_services)]) -> dict[str, bool]:
    """Report which providers are configured."""
    return {"embedder": services.embedder is not None, "llm": services.llm is not None}


def create_app(services: Services) -> FastAPI:
    """Build the FastAPI app around injected services."""
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Verify the migrated schema at startup; an unreachable or unmigrated database fails startup loudly."""
        conn = await connect()
        try:
            await require_schema_revision(conn)
        finally:
            await conn.close()
        log.info("schema_verified")
        yield

    app = FastAPI(title="multi-tenant-agentic-rag", lifespan=lifespan)
    install_request_id_logging()

    @app.middleware("http")
    async def correlate(request: Request, call_next: Any) -> Any:
        """Bind one request id to every log line of this request and echo it as X-Request-ID."""
        rid = resolve_request_id(request.headers.get("x-request-id"))
        token = request_id.set(rid)
        tp_token = traceparent.set(resolve_traceparent(request.headers.get("traceparent")))
        try:
            response = await call_next(request)
        finally:
            traceparent.reset(tp_token)
            request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    app.state.services = services
    app.include_router(router)
    app.include_router(v1)
    return app
