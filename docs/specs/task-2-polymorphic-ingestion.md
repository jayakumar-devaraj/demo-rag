# Task 2 — Polymorphic ingestion

**Purpose.** Abstract `Worker` interface; PDF (PyMuPDF, table grids → Markdown), XLSX (openpyxl,
one unit per tab, headers kept), DOCX (python-docx, headings/paragraphs/inline tables), TXT workers.
SHA-256 over normalized Markdown per page/tab before chunking; structure-aware chunker (tables kept
whole with their header row; paragraphs split on headings/blank lines); wired to Task 1 repo.

**Inputs/outputs.** In: file bytes + filename + tenant. Out: per-unit `IngestResult` (status, hash).

**Verification.** Unit: each worker on a generated fixture file (multi-tab XLSX keeps row/column
coordinates); chunker never separates a table cell from its header. Integration: re-ingesting a
renamed file with one modified row → only that page re-embedded, stale chunks purged.
