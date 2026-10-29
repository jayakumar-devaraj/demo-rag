# Summary

## What was built

A prototype question-answering service over company documents: PDFs, Excel workbooks, Word files and
plain text. Each organisation (tenant) can only ever search its own documents. The service:

- **Keeps tables intact.** Every page or spreadsheet tab becomes a clean table-preserving text
  version. Excel row numbers and column letters survive, so an answer can point to an exact cell.
- **Avoids paying twice for the same content.** Each page gets a fingerprint. Re-uploading an
  unchanged or merely renamed file costs no reprocessing, and a changed page replaces its old
  version completely in one step, so outdated figures cannot linger.
- **Finds both meaning and exact codes.** Search combines "what the question means" with exact
  matching of IDs such as invoice or serial numbers.
- **Checks its own work before answering.** If information is missing, it searches again for the
  specific missing items. If it still cannot answer, it says so rather than guessing. An answer that
  quotes a number not found in the source documents is blocked.
- **Scores every answer** on three quality measures: were the right documents found, is the answer
  supported by them, and does it address the question. The scores are logged.

- **Serves other applications.** `POST /v1/retrieve` returns the passages that answer a question,
  without writing an answer, so the chatbot can compose its own reply. It takes the caller's deadline.
- **Keeps customers apart twice.** Every query is limited to the caller's organisation in the search
  itself, and the database enforces the same rule, so a missed filter in the code cannot leak data.
- **Accepts signed tokens** from a company identity provider, with separate rights to search and to
  upload; the old fixed token list still works until it is switched off.
- **Keeps a durable record of use.** Each upload, search and question writes an audit row, and an
  upload and its record succeed or fail together. A search is never returned without its record.

The first version was built on 2026-09-24 in about an hour inside a two-hour window and verified end to
end against a real AI model (Groq): all three demonstration scenarios gave correct, cited answers and
every answer was scored for quality. The retrieval endpoint, container image, database migrations,
audit trail, row-level security and signed tokens were added from 2026-10-25 to 2026-10-29.

**Evidence.** The figures below are generated from the repository by `scripts/gen_figures.py`, and CI
fails when they drift.

<!-- figures:start -->
- **Tests collected:** 129 (of which 36 need the real Postgres with pgvector); calls to real model providers are a separate, opt-in `live` set.
- **Coverage:** 92 % of 906 statements. Report header: "Coverage at 1a75804 from CI run 40926258798 (all tests except live)".
<!-- figures:end -->

## The most important trade-off

**One database instead of two.** Document records and the search index live in the same PostgreSQL
database. A separate, specialised search database would scale further (roughly past a million text
fragments). But then updating a document would span two systems, and a failure halfway through could
leave stale data that returns wrong answers. With one database, each update either fully happens or
does not happen at all. At this stage, correctness was worth more than scale.

## Remaining work, in priority order

1. **Run it against a real identity provider.** Signed-token checks are tested with generated keys and
   a local key endpoint only, and the fixed token list is still accepted by default.
2. **Verify monitoring (Langfuse) and the backup AI provider (OpenAI) live.** Both are wired in but
   have never been exercised against a real service.
3. **Limits and retention.** There are no per-customer rate limits or quotas, and the audit table has
   no partitioning or retention job.
4. **Improve PDF layout handling** beyond automatic table detection. This was cut for time.
5. **Plan for scale** if document volume grows past roughly a million fragments.
6. **Add a deployment target** (the image is published, but nothing deploys it).

Details are in `docs/known_limitations.md`.
