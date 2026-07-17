# Approach Document

Honest engineering notes for the CardioTrack Document Intelligence MVP.

This is not a claim that the system is production-grade MedTech software. It is a clear, interview-explainable backend that solves the assignment end-to-end.

---

## 1. Problem framing

AffineSurge builds AI-powered document intelligence for MedTech. The assignment asks for a backend that:

1. Reads a medical device PDF
2. Builds a hierarchical document tree
3. Stores it in SQLite with versioning
4. Diffs versions by content
5. Lets users select sections (version-pinned)
6. Sends selected text to Gemini to generate QA test cases
7. Stores outputs
8. Detects when those outputs become stale after document changes

Reviewers care more about **why** each choice was made than about framework fashion.

---

## 2. Parser strategy

**Library:** PyMuPDF (`fitz`) — already reliable for text extraction on these PDFs.

**Pipeline:**

1. Extract text page by page
2. Normalize Unicode (ligatures like `ﬁ` → `fi`, dashes, quotes)
3. Detect numbered headings with regex: `^(\d+(?:\.\d+)*)\.?\s+(.+)$`
4. Everything after a heading until the next heading becomes that node’s body
5. Flatten table-like short line runs into `" | "`-joined text
6. Compute `content_hash = SHA-256(normalize(heading + body).lower())`

**Why regex headings instead of only font size?**  
Font size helps, but section numbers are the stable identity across versions. Numbering also gives parent relationships for free.

### Hierarchy reconstruction

- `level` = number of dotted parts (`2.1.1.1` → level 4)
- Ideal parent of `2.1.1.1` is `2.1.1`
- If that parent is missing (**skipped hierarchy**), walk upward until an existing ancestor is found → parent becomes `2.1`

This matches the assignment PDF, which jumps from `2.1` to `2.1.1.1`.

### Edge cases handled

| Case | Handling |
|---|---|
| Skipped hierarchy | Ancestor walk |
| Duplicate headings | Allowed; uniqueness is `section_number` within a version (`4.2` and `7.1` both “Error Codes”) |
| Out-of-order numbering | Keep PDF order via `sort_order` (`3.4` appears before `3.3`) |
| Ligatures | Explicit map + NFKC |
| Tables | Short-line runs flattened into readable text |

### Limitation

Numbered body lists like classification items can look like headings. A small heuristic skips lines that look like enum items (`Normal:`, long titles with `:`). This is corpus-tuned, not universal.

---

## 3. Version matching strategy

- A **Document** has a stable `document_key` (e.g. `ct200_manual`)
- Each ingest creates a new **DocumentVersion** (`v1`, `v2`, …)
- Re-ingesting the same `(document_key, version_label)` is **rejected** — versions are immutable
- Nodes across versions are matched by **`section_number`**, not by database id
- Change detection compares **`content_hash`**

Filename helper: `ct200_manual_v2.pdf` → key `ct200_manual`, label `v2`.

---

## 4. Hashing strategy

Hash includes heading + body after normalization/lowercasing.

**Why?**  
Whitespace and ligature differences should not create false diffs. Meaningful wording changes (battery cycles 300→250, inflation step 40→30 mmHg, new `5.3`) must change the hash.

We do **not** hash page layout or raw PDF bytes — the assignment cares about document *content* evolution.

---

## 5. JSON vs MongoDB for generated outputs

**Choice:** SQLite row for metadata + JSON file for the full QA payload.

**Why JSON files:**

- Zero extra infrastructure for an internship MVP
- Easy to open during a demo or debug session
- Prompt/schema can evolve without migrations
- Relational integrity still lives in SQLite (selections, nodes, versions)

**Why not MongoDB (yet):**

- Overkill for one nested document type
- Adds ops cost and another failure mode
- Would help later if we needed flexible queries over many nested fields at scale

Interview line: *“I kept the system of record relational and treated the LLM payload as a document artifact.”*

---

## 6. Prompt design

The prompt:

- Sets a MedTech QA engineer role
- Restricts answers to provided section text (reduces hallucination)
- Asks for **3–5** executable test cases
- Demands a fixed JSON schema (`id`, `title`, `precondition`, `steps`, `expected_result`, `priority`, `source_sections`)
- Requests JSON only (and we also set `response_mime_type=application/json` when using Gemini)

Temperature is low (`0.2`) for more stable structure.

---

## 7. Structured output validation + retry

Even with JSON mode, models can:

- return too few/many cases
- omit fields
- wrap JSON in markdown fences
- return empty candidates

**Validation:** schema checks in `validate_test_cases`.

**Recovery:**

1. Strip fences / extract first JSON object
2. Validate
3. Retry up to `GEMINI_MAX_RETRIES` with short backoff
4. Persist a `failed` generation record with error text instead of crashing the API process

---

## 8. Selection + generation + staleness

**Selections are version-pinned:** every `node_id` must belong to the selection’s `version_id`. This prevents silently mixing v1 and v2 text.

**At generation time** we snapshot `{section_number: content_hash}` into `generations.source_hashes_json`.

**Staleness:** compare those hashes to the same section numbers in a later version (default: newest later version of the same document). If any selected section was modified or removed → `is_stale=true`.

Retrieval returns:

- generated QA (from JSON file)
- stale/current status
- affected source document version metadata

---

## 9. Engineering decisions (summary)

| Decision | Choice | Reason |
|---|---|---|
| API framework | FastAPI | Typed, fast to demo, good OpenAPI |
| ORM | SQLAlchemy 2.x | Clear models, SQLite-friendly |
| DB | SQLite | Assignment requirement; simple deploy |
| PDF | PyMuPDF | Accurate text extract on provided manuals |
| Identity across versions | `section_number` | Stable human-meaningful key |
| LLM | Gemini | Assignment requirement |
| Output store | JSON files + SQLite meta | Simple, inspectable |
| Diff | Content hash | Deterministic, explainable |

---

## 10. Limitations

- Parser assumes dotted numeric headings (fine for these manuals, not every PDF)
- Search is substring match, not ranked full-text search
- No auth / multi-tenancy
- Gemini quality depends on key/model quotas and prompt adherence
- Staleness is section-hash based; it does not semantically judge whether a wording tweak invalidates a test
- Table flattening is heuristic
- Uses `google-generativeai` (widely documented); Google now recommends migrating to `google.genai` later

---

## 11. Future improvements

- True PDF table extraction (structure cells → markdown tables)
- Font-size + numbering hybrid heading detector
- SQLite FTS5 for search
- Async Gemini calls + job queue for long generations
- Semantic staleness (embed old vs new section, threshold)
- Migrate to official `google.genai` SDK
- Alembic migrations if schema grows

---

## 12. Decision log

| # | Decision | Alternatives considered | Why this one |
|---|---|---|---|
| 1 | Regex section parser | Font-only, LLM-to-tree | Deterministic, cheap, testable |
| 2 | Immutable versions | Update-in-place | Traceability; required by assignment spirit |
| 3 | Hash heading+body | Hash body only / raw PDF | Catches retitles; ignores binary noise |
| 4 | JSON artifacts | MongoDB, all-in-SQLite Text only | Balance of queryability + inspectability |
| 5 | Version-pinned selections | Free-floating node ids | Prevents cross-version bugs |
| 6 | Hash snapshot for staleness | Re-parse selection text always | Explicit audit of what the model saw |
| 7 | Validate then retry Gemini | Trust model output | Malformed JSON is common; must handle it |
| 8 | Keep code shallow | Heavy DDD / many abstractions | Interview explainability for a student MVP |

---

## 13. How to explain this in an interview (30 seconds)

> “I built a small document-intelligence backend: PyMuPDF + regex rebuilds a section tree, SQLite stores immutable versions, and content hashes drive diffs. Users pin sections to a version, Gemini returns validated JSON test cases stored as files, and staleness is just ‘did any selected section hash change in a later version?’ I chose boring, explainable pieces on purpose.”
