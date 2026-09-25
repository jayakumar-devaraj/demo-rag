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

It was built in about an hour inside a two-hour window. There are 56 automated tests (91% of the code
exercised), and they run automatically on every change. It has also been verified end to end against a
real AI model (Groq): all three required demonstration scenarios produced correct, cited answers, and
every answer was scored for quality.

## The most important trade-off

**One database instead of two.** Document records and the search index live in the same PostgreSQL
database. A separate, specialised search database would scale further (roughly past a million text
fragments). But then updating a document would span two systems, and a failure halfway through could
leave stale data that returns wrong answers. With one database, each update either fully happens or
does not happen at all. At this stage, correctness was worth more than scale.

## Remaining work, in priority order

1. **Verify monitoring (tracing) against a live Langfuse server.** It's wired in, but it needs a
   running Langfuse instance and access keys.
2. **Verify the backup AI provider.** OpenAI is configured as the fallback if Groq is unavailable, but
   that account currently has no credit, so the fallback hasn't been exercised live.
3. **Improve PDF layout handling** beyond automatic table detection. This was cut for time.
4. **Replace the demo login** (a fixed list of access tokens) with the company's identity provider.
5. **Plan for scale** if document volume grows past roughly a million fragments.

Details are in `docs/known_limitations.md`.
