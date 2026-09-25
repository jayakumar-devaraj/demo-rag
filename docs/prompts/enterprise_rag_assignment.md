# Interview Assignment: Build a Polymorphic Agentic RAG System

### 1. Objective
Build a working prototype that transforms heterogeneous enterprise documents (**PDFs, Excel Spreadsheets, Word documents, and text files**) into precise, verifiable analytical answers using a polymorphic ingestion layout and a self-correcting execution model. Demonstrate document-type abstraction, two-tier deterministic SHA-256 deduplication, multi-mode data retrieval, and rigorous validation/hallucination controls. Focus on end-to-end data lifecycle automation with controlled engineering autonomy.

### 2. Scenario
You will build an enterprise-grade Retrieval-Augmented Generation (RAG) system from scratch optimized for parsing complex multi-page tables, formulas, and multi-tab spreadsheets without losing spatial, hierarchical, or row-column matrix dependencies across varying file formats. 

Your task is to complete and improve it over a compressed timeframe using AI assistance while demonstrating rigorous architectural judgment, tenant data separation, and automated state tracking.

### 3. Scope
*   **Polymorphic Data Ingestion:** Extracting multi-page digital PDF grids, multi-tab structural spreadsheets (.xlsx), and semantic text flows (.docx) into a single unified context format.
*   **State Tracking & Memory Purging:** Eliminating processing inflation, orphan vectors, and redundant infrastructure computation during file updates, rewrites, or filename modifications.
*   **Hybrid Dense-Sparse Search:** Balancing semantic user intent with strict, precise string or alpha-numeric key matching (IDs, financial entries, serial numbers).
*   **Adaptive Context Routing:** Dynamically validating context sufficiency and executing secondary re-query loops for complex, multi-stage analytical questions.

---

### 4. Core Requirements

#### 1) Polymorphic Ingestion Interface & Document-Type Agnostic Extraction
Architect an abstract ingestion interface that flattens data structures according to their native data types, resolving all formats down to structured Markdown before downstream manipulation:
*   **PDF Worker:** Isolate layout coordinates and preserve tabular row alignments as Markdown grids.
*   **Spreadsheet Worker:** Parse multi-tab matrix sheets dynamically, preserving horizontal and vertical headers as clean Markdown matrices.
*   **Document Worker:** Flatten paragraphs, inline matrices, and section headings into clean, hierarchical text strings.

#### 2) Two-Tier SHA-256 Verification & State Filtering
Prevent redundant resource consumption and secure the data ingestion boundary against duplicate or renamed document uploads:
*   Compute an immutable 64-character SHA-256 signature hash of each individual normalized page layout or spreadsheet tab *after* converting it to Markdown but *before* chunking.
*   Query a central relational `page_registry` using this hash. If the signature exists, bypass text splitting and downstream embedding generation entirely for that page/tab.

#### 3) Relational Vector Purging & Clean Upserts
Eliminate the risk of stale or "ghost" vector shards polluting your database when a page or document tab is modified and re-uploaded:
*   If a page signature fails the SHA-256 gatekeeping check (indicating changed content), the state controller must execute a transactional cascading deletion (`DELETE`) of all historical child chunks linked to that page state.
*   Enforce transactional database integrity (ACID) during updates to guarantee that obsolete vector nodes are wiped out before new ones are committed.

#### 4) Multi-Tenant Security & Access Control Gates
Secure your data platform against information leakage at the retrieval layer:
*   Embed strict multitenancy isolation keys directly into the vector namespace and relational schema. Every ingested page, tab, and child chunk must be structurally bound to a unique tenant or user session identifier.
*   The hybrid retrieval engine must enforce mandatory pre-filtering so queries only evaluate chunks matching the caller's active session profile, preventing unauthorized cross-tenant data leaks.

#### 5) Semantic Structure-Aware Chunking
Convert extracted data streams into discrete components based on shifts in contextual meaning rather than arbitrary text lengths or static token footprints. 
*   Dynamically establish chunk boundaries matching the physical layout of raw tables and paragraphs so cell entries are never orphaned from their structural headers.
*   Map all child text vectors explicitly back to their unbroken parent page or spreadsheet tab structure within the database layer using explicit foreign keys.

#### 6) Dense-Sparse Hybrid Retrieval
Mitigate the inherent failure modes of standard semantic vector operations when processing numerical data, financial sheets, or specific structural keys:
*   Execute parallel retrieval pipelines containing a dense vector distance lookup matching general intent and a sparse keyword text search matching exact query identifiers.
*   Fuse multi-mode retrieval rankings into a single sorted baseline using a weight-normalized rank calculation (e.g., Reciprocal Rank Fusion).
*   Assemble complete parent sections (the entire unbroken markdown table or page string) dynamically using child key references before delivery to the synthesis stage.

#### 7) Agentic Reasoning & Self-Correction Loop
Design and implement an orchestration layer that coordinates stateful validation steps between retrieval and answer synthesis to handle complex, multi-stage judge prompts:
*   **Evaluation Checkpoints:** The synthesis engine must inspect the retrieved data package prior to final output generation to determine if interdependent values or missing parameters are required.
*   **Re-Query Routing:** If data dependencies span non-contiguous pages, files, or tabs, the system must trigger an automated secondary search pass for missing identifiers, appending the new matrix context onto the prompt window dynamically.
*   **Control Gates:** Enforce a fallback command outputting an absolute data absence message if context variables remain structurally insufficient after correction loops.

#### 8) Programmatic RAG Triad Validation Framework
Incorporate an automated evaluation sub-routine to algorithmically verify generation performance. The output pipeline must calculate and log system performance across three criteria before rendering final answers:
*   **Context Relevance:** Quantify whether the retrieved tables and paragraphs directly align with the core requirements of the user query.
*   **Groundedness:** Algebraically confirm that the final response derives strictly from the source texts and completely avoids hallucinating cells, numbers, or trends.
*   **Answer Relevance:** Assess whether the generated text addresses the user's explicit question without adding unnecessary padding.

---

### 5. Deliverables
*   **Working Prototype:** A runnable end-to-end application including polymorphic ingestion pipelines, a multi-tenant hybrid search layout, and an interactive chat interface.
*   **Architecture Overview:** Full structural map detailing the data abstraction layer, SHA-256 signature matrices, hybrid ranking logic, cascading delete controls, and agentic loop gates.
*   **Verification Scenarios:** Three live validation proofs:
    1. A multi-tab Excel spreadsheet ingest demonstrating matrix coordinate retention.
    2. A renamed PDF containing a single modified data row demonstrating stale vector purging and active caching.
    3. An ambiguous query requiring a self-correcting multi-page lookup across both document types.
*   **Setup Instructions:** Container configurations, asset environments, and automated schema provision scripts.

### 6. Evaluation Criteria
*   **Polymorphic Parsing Effectiveness:** Seamless handling of multiple corporate document formats and accurate translation into aligned structural Markdown.
*   **Architecture & System Design Quality:** Clean isolation of the data abstraction layer, verification signatures, and cascading connection controls.
*   **Security & Multi-Tenant Rigor:** Zero cross-pollination of data or metrics across varying user context spaces during hybrid query execution.
*   **Depth of Self-Correction Execution:** Realism, speed, and logical defensibility of the multi-pass agentic query routing loop.
