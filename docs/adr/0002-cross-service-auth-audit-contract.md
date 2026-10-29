# 0002 · Cross-service auth, audit and contract with the chatbot

Status: **Accepted (Round 2 of 2, 2026-10-28).** Round 1 below is kept as written; the final
decision is "Decision (Round 2)".
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

## Decision (Round 2)

The lead reviewed Round 1 and the agent's recommendations in chat on 2026-10-28 and agreed to them.
Points 1–2 of Round 1 stand. Points 3–6 are accepted, with these answers to the open questions:

- **A. Token shape: per-tenant client credentials now; token exchange later.** The chatbot's worker
  presents a token from a per-tenant OIDC client whose `tenant` claim and `rag:query` scope are fixed
  by the identity provider. This service accepts only tokens from an issuer allow-list, checks issuer
  and audience, accepts only the configured signing algorithms (never `none`, never a shared-secret
  algorithm), caches the JWKS, and answers 503 when the keys cannot be fetched. Token exchange
  (RFC 8693) replaces this when the chatbot's users move to OIDC.
- **B. Ingest callers.** Ingest tokens are issued to a pipeline service principal, not to people;
  human uploads go through that pipeline. The audit row records the caller (`sub`) and any acting
  identity.
- **C. Audit.** The audit row is written in the same transaction as the ingest or delete it
  describes, so the action and its record commit or roll back together. Default retention is 400 days
  queryable, partitioned by month, with older partitions archived. This default is not a compliance
  determination and is replaced by the real requirement when it is confirmed.
- **D. Migrations.** A one-shot migration job runs under a separate owner role, using Alembic in a
  migration image only; the API image does not include it. The API connects as a non-owner role.
  Tables use `FORCE ROW LEVEL SECURITY`, because the table owner otherwise bypasses the policies.
- **Rollout order:** this repository first (migrations, role split and `audit_log`; row-level security
  with database-level cross-tenant tests; then JWT validation accepting static and JWT tokens during
  the overlap), then the chatbot, then removal of static tokens.
- **Check before relying on RLS:** benchmark pgvector queries with the policies on, since policies can
  change the query plan.

## Consequences

- The identity provider becomes a dependency of every call. If the JWKS is unreachable the service
  answers 503 and never falls back to a static token.
- Moving to RLS needs a non-owner application role and a separate migration role.
- The chatbot must change its credentials before static tokens can be removed; the two repositories
  release in the order RAG first, chatbot second, with both token types accepted during the overlap.
- This record changes no running behaviour; implementation is split into separate pull requests per
  decision after Round 2.

## Amendment (2026-10-28, step 3: row-level security)

Round 1 proposed `SET LOCAL app.tenant_id` inside a transaction per request. As built, the tenant is
bound with `set_config('app.tenant_id', <tenant>, false)` (session level) right after the connection
opens (`connect(tenant_id)` in `src/db/connection.py`). Reason: connections are autocommit and retrieval
runs many statements across LLM calls; one request-long transaction would hold a connection and
snapshot open for the whole model call. This is safe only because each request opens and closes its own
connection; if connections are ever pooled, the setting must be reset on release.

Policies (migration 0003): `page_registry` and `chunks` are `ENABLE`d and `FORCE`d with a policy that
compares `tenant_id` to `current_setting('app.tenant_id', true)` for both reads and writes; `audit_log`
has a `SELECT` policy and an `INSERT` policy. An unbound connection compares against NULL and sees no
rows. The API role must never be a superuser or have `BYPASSRLS`; superusers, including the compose
owner role used by the migrate job, bypass the policies. `docs/reports/rls-benchmark-2026-10-28.md`
records the check that the policies do not change the plan or add measurable latency at 20,000 chunks.
