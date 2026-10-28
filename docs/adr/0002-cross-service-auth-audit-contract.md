# 0002 · Cross-service auth, audit and contract with the chatbot

Status: **Proposed (Round 1 of 2).** Round 2 records the lead's critique and the final decision.
The matching record in the chatbot repository is its ADR 0011 (same title).

## Context

This service is called by the chatbot (ADR 0001 defines `/v1/retrieve`). Four things cross the
boundary and are decided today in each repository on its own:

- **Authentication.** The tenant comes only from the bearer token, resolved from a static
  `TENANT_TOKENS` list in the environment. There is no expiry, rotation or scope, and ingest and
  query use the same token.
- **Tenant isolation.** Every read applies `WHERE tenant_id = …` taken from the authenticated
  session (`src/retrieval/hybrid.py`). The database itself enforces nothing: a statement that omits
  the predicate would read across tenants.
- **Audit.** Requests carry an `X-Request-ID` that is written into JSON logs. There is no durable,
  queryable record of who ingested or retrieved what.
- **Contract.** `docs/openapi-v1.json` here and `docs/contracts/rag-openapi-v1.json` in the chatbot
  were byte-identical on 2026-10-28, and a unit test checks drift against the generated contract
  here, but nothing compares the two repositories.

## Decision (Round 1)

Decided by the lead in chat on 2026-10-28:

1. **Auth is OIDC/JWT validated against a JWKS.** Tokens are short-lived and carry a tenant claim and
   scopes (`rag:query`, `rag:ingest`). A local identity provider runs in compose for development and
   tests. Static `TENANT_TOKENS` is removed after migration.
2. **The durable audit trail is an append-only Postgres table** in this service's database. The
   application role has `INSERT` and `SELECT` only.

Proposed for critique:

3. **Row-level security as a second layer.** The application role is not the table owner. Each
   request runs in a transaction that sets `app.tenant_id` with `SET LOCAL`; policies on `chunks`
   and `page_registry` compare `tenant_id` to it. The existing `WHERE` clauses stay. Tests prove a
   statement without the predicate returns no other tenant's rows.
4. **Audit rows** record: tenant, subject (`sub` claim), action (`ingest`, `retrieve`, `chat`,
   `delete`), document key, outcome, `X-Request-ID`, `traceparent`, timestamp. A tenant-scoped query
   endpoint reads them.
5. **Contract check in CI.** A job fetches the chatbot's copy of the contract at its pinned release
   and fails on any difference. The `/v1` surface follows semantic versioning; a breaking change
   needs a new path prefix.
6. **Release.** Each `v*` tag publishes an image whose digest is reported in the release notes, so the
   chatbot can pin `name@sha256:<digest>`.

## Open questions for Round 2

- **A. Token shape the chatbot presents.** Per-tenant client credentials, token exchange (RFC 8693),
  or one service principal plus a signed tenant assertion. This service must accept whichever is
  chosen, so the claim names (`tenant`, `scope`) and the issuer allow-list are fixed by that answer.
- **B. Ingest callers.** Are ingest tokens issued to people, to a pipeline, or both? It changes the
  `sub` recorded in the audit table.
- **C. Audit retention period and recovery-point objective.**
- **D. Migration tool.** The schema is applied at startup today; RLS and the audit table are the first
  changes that need versioned migrations. A one-shot migration job (for example Alembic) is proposed.

## Consequences

- The identity provider becomes a dependency of every call. If the JWKS is unreachable the service
  answers 503 and never falls back to a static token.
- Moving to RLS needs a non-owner application role and a separate migration role.
- The chatbot must change its credentials before static tokens can be removed; the two repositories
  release in the order RAG first, chatbot second, with both token types accepted during the overlap.
- This record changes no running behaviour; implementation is split into separate pull requests per
  decision after Round 2.
