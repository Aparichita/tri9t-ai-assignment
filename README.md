# CardioTrack Document Intelligence API

Backend MVP for **AffineSurge (Tri9T AI)** internship assignment: ingest medical-device PDFs into a versioned document tree, select sections, generate QA test cases with Gemini, and detect when those outputs go stale after document changes.

## Features

- PDF → hierarchical section tree (heading, section number, level, parent, body, content hash)
- SQLite + SQLAlchemy persistence (versions are immutable)
- Version compare via content hashes (`ct200_manual.pdf` vs `ct200_manual_v2.pdf`)
- Version-pinned section selections
- Gemini QA generation (3–5 structured test cases) with validation + retry
- Generated outputs stored as JSON files (with SQLite metadata)
- Staleness detection after later document versions change selected text

## Project structure

```
AI_ASSIGNMENT/
├── app/
│   ├── main.py                 # FastAPI entrypoint
│   ├── config.py               # Settings / env
│   ├── database.py             # SQLAlchemy engine + session
│   ├── models.py               # ORM models
│   ├── schemas.py              # Pydantic request/response models
│   ├── parser/pdf_parser.py    # PDF hierarchy parser
│   ├── services/
│   │   ├── document_service.py # Ingest, compare, selection, staleness
│   │   └── gemini_client.py    # Prompting + validation + retries
│   └── routers/                # HTTP endpoints
├── data/
│   ├── ct200_manual.pdf        # Assignment PDF v1 (do not modify)
│   └── ct200_manual_v2.pdf     # Assignment PDF v2 (do not modify)
├── tests/
├── postman/CardioTrack_API.postman_collection.json
├── APPROACH.md
├── requirements.txt
└── .env.example
```

## Setup

### 1. Create a virtual environment (recommended)

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # macOS / Linux
```

Edit `.env`:

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes (for generation) | API key from [Google AI Studio](https://aistudio.google.com/apikey) |
| `GEMINI_MODEL` | No | Default `gemini-2.0-flash` |
| `DATABASE_URL` | No | Default `sqlite:///./data/app.db` |
| `GENERATIONS_DIR` | No | Default `./data/generations` |

### 4. Run the API

From the project root:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

## Versioning workflow (happy path)

1. **Ingest v1**

```http
POST /documents/ingest
{
  "file_path": "data/ct200_manual.pdf",
  "document_key": "ct200_manual",
  "version_label": "v1"
}
```

2. **Ingest v2** (never overwrites v1)

```http
POST /documents/ingest
{
  "file_path": "data/ct200_manual_v2.pdf",
  "document_key": "ct200_manual",
  "version_label": "v2"
}
```

3. **List sections / inspect a node / search**

```http
GET /versions/{version_id}/sections
GET /nodes/{node_id}
GET /versions/{version_id}/search?q=overpressure
```

4. **Compare versions by content hash**

```http
GET /versions/compare?version_a_id=1&version_b_id=2
```

5. **Create a version-pinned selection**

```http
POST /selections
{
  "version_id": 1,
  "node_ids": [12, 15],
  "name": "inflation-and-safety"
}
```

6. **Generate QA test cases (Gemini)**

```http
POST /generations
{ "selection_id": 1 }
```

7. **Retrieve generation + staleness**

```http
GET /generations/{generation_id}
GET /generations/{generation_id}/staleness
GET /generations/{generation_id}/staleness?against_version_id=2
```

If selected section text changed in v2, the generation is marked **stale**.

## API overview

| Method | Path | Purpose |
|---|---|---|
| POST | `/documents/ingest` | Parse PDF into a new immutable version |
| GET | `/documents` | List documents |
| GET | `/documents/{id}/versions` | List versions for a document |
| GET | `/versions/{id}/sections` | List section tree nodes |
| GET | `/nodes/{id}` | Node details + children |
| GET | `/versions/{id}/search?q=` | Full-text-ish search over heading/body |
| GET | `/versions/compare` | Diff two versions by section + hash |
| POST | `/selections` | Create version-pinned selection |
| GET | `/selections/{id}` | Retrieve selection |
| POST | `/generations` | Call Gemini, store QA JSON |
| GET | `/generations/{id}` | Retrieve QA + stale/current status |
| GET | `/generations/{id}/staleness` | Explicit staleness check |
| GET | `/health` | Liveness |

## Testing

```bash
python -m pytest tests/ -v
```

Parser tests cover:

- skipped hierarchy (`2.1.1.1` → parent `2.1`)
- duplicate headings (`Error Codes` under `4.2` and `7.1`)
- out-of-order numbering (`3.4` before `3.3` in document order)
- ligature normalization and table flattening
- real hash diffs between the two assignment PDFs

API tests cover ingest, immutability, search, compare, and version-pinned selections **without** calling Gemini.

## Postman

Import `postman/CardioTrack_API.postman_collection.json` into Postman.

1. Set collection variable `baseUrl` = `http://127.0.0.1:8000`
2. Run **Ingest v1** then **Ingest v2**
3. Copy `version_id` / `node_id` / `selection_id` into later requests (or use the collection’s script variables where provided)

## Why JSON files (not MongoDB)?

For this MVP, generated QA payloads are written under `data/generations/*.json` while SQLite stores IDs, status, model name, and source hashes.

- No extra database to install or operate
- Easy to open and inspect during debugging / demos
- Payload shape may evolve with the prompt; JSON files stay flexible
- SQLite remains the source of truth for relational data (documents, nodes, selections)

MongoDB would help if we needed rich ad-hoc queries over nested test-case fields at scale. That is unnecessary here.

## Design notes (short)

See **[APPROACH.md](APPROACH.md)** for parser strategy, hashing, prompt design, retries, tradeoffs, limitations, and a decision log you can walk through in interviews.

## Suggested git commits

When you are ready to commit milestones (do not fake dates):

1. `chore: initialize FastAPI project scaffold`
2. `feat: implement PDF parser with hierarchy and hashing`
3. `feat: add SQLAlchemy models and ingest API`
4. `feat: implement search and version compare`
5. `feat: implement version-pinned selection API`
6. `feat: integrate Gemini QA generation with validation`
7. `feat: implement staleness detection and retrieval`
8. `test: add parser and API unit tests`
9. `docs: add README, approach document, and Postman collection`
