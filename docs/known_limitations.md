# Known Limitations

- **Task 2 cut (agreed 21:57):** TXT files go through the DOCX text path, with no separate worker. PDF tables are detected with PyMuPDF `find_tables()` only; there's no extra layout-coordinate handling. Reason: 5-minute drift overrun at the Task 1 checkpoint.
- **No model credentials (option c, agreed 21:57):** no embedding or LLM provider key is available. Tests use a labeled test-double embedder, and every real LLM path (synthesis, sufficiency check, Ragas scoring) is UNEXECUTED.
