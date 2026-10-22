# AI Research Assistant — Backend

FastAPI + PostgreSQL/pgvector + Ollama (`qwen2.5:3b`, generation only) + local Hugging Face embeddings (`BAAI/bge-large-en-v1.5`, 1024 dims, via sentence-transformers). Answers questions about an indexed corpus of research PDFs, with application-validated citations (paper title, DOI/URL, real PDF page number).

## Verification status (read first)

| Area | Status |
|---|---|
| Chunking, evidence selection, prompt building, citation validation, RAG orchestration | **Unit-tested, passing** (stdlib `unittest`, synthetic fixtures, fake embedder/LLM/in-memory store) |
| Embedder wrapper (fake model: dims, NaN/inf, order, query instruction, device, errors), resumable re-index logic | **Unit-tested, passing** (49 Python tests ran and passed; 14 more are skipped until dependencies or a test database exist) |
| Ollama client (mocked HTTP) | Tests written; **not run** (httpx not installable offline) |
| Real BGE model download/inference, GPU | **Not tested** |
| Migration 0003, pgvector search, SQL re-index (tests in `tests/integration/`) | Written; **skipped without `TEST_DATABASE_URL`, never run** |
| FastAPI routes, error envelope | Tests written; **not run** |
| PostgreSQL / pgvector store, models, Alembic migration, ingestion pipeline, PyMuPDF extraction | Code written; **never executed** |
| Live Qwen 2.5 3B via Ollama | **Not tested** |
| Real research papers | **Not tested** |

Supabase is supported as a plain PostgreSQL host via `DATABASE_URL`; no Supabase client library, Auth, or Storage is used yet (see docs). Migrations: `0001` schema, `0002` RLS, `0003` vector(1024) for BGE.

Treat everything not marked "passing" as a first draft that needs its first real run.

## Documentation
| Doc | Contents |
|---|---|
| [frontend/README.md](frontend/README.md) | Web UI (HTML/CSS/vanilla JS): run, structure, tests, limits |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Audit of what exists, architecture, data flow |
| [docs/SUPABASE_INTEGRATION.md](docs/SUPABASE_INTEGRATION.md) | Supabase database setup, connection strings, schema, storage plan |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Verified endpoints, errors, curl examples |
| [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) | Windows step-by-step setup and troubleshooting |
| [docs/EMBEDDINGS.md](docs/EMBEDDINGS.md) | BGE embeddings, query instruction, GPU/CPU, re-indexing, verification |
| [docs/PDF_INGESTION_AND_RAG.md](docs/PDF_INGESTION_AND_RAG.md) | Corpus import, chunking, retrieval, citations |
| [docs/SECURITY.md](docs/SECURITY.md) | Secrets, RLS, auth design, production checklist |

## Web UI
Start the backend (`uvicorn app.main:app --reload`) and open **http://localhost:8000/**. Pages: Research Chat, Library, Upload, Jobs, System Status, Settings. No Node/npm is needed to run it. See `frontend/README.md`. Tested so far: 28 Node unit tests with mocked fetch (passing); DOM tests in `frontend/tests/browser.html` and all real-backend behaviour are **not yet run**.

## Setup

```bash
# 1. Ollama (https://ollama.com/download), then:
ollama pull qwen2.5:3b
python -m scripts.download_model   # after pip install: downloads BAAI/bge-large-en-v1.5 (~1.3 GB) once
ollama list                      # both should appear

# 2. Database
docker compose up -d db          # pgvector/pgvector:pg16

# 3. Python 3.11+
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 4. Schema
alembic upgrade head

# 5. Run
uvicorn app.main:app --reload    # docs: http://localhost:8000/docs
curl localhost:8000/api/health   # reports ollama up/unreachable, model available/missing, db status
```

## Importing the corpus

```bash
python -m scripts.import_corpus --dir data/pdfs                 # titles default to file names
python -m scripts.import_corpus --manifest corpus.json          # real metadata (see script docstring)
```
Re-running is safe: PDFs are keyed by SHA-256, so already-indexed files are skipped. Scanned PDFs with no text are marked `needs_ocr` (OCR is not implemented). Upload one-off PDFs with `POST /api/papers/upload` and poll `GET /api/jobs/{id}`.

## Chat

```bash
curl -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"question":"What limitations do the authors report?","paper_ids":[],"top_k":5}'
```
Response shape: `answer, citations[{id, paper_id, title, authors, page_number|null, section, doi, source_url, relevance_score}], retrieved_chunks, model, insufficient_evidence, warnings`.
`warnings` may contain `invalid_citations_removed`, `no_valid_citations`, `evidence_truncated_to_fit_context`.

## How citations work
Retrieved chunks get ids `E1..En` assigned by the app; the model must cite them as `[E1]`. Unknown ids are stripped; valid ids are resolved from the database. Unknown pages return `null`. **A valid id only proves that chunk was supplied to the model, not that it supports the sentence**; claim-to-evidence alignment needs separate evaluation.

## Embedding model changes
Embeddings are BGE (1024-dim) computed locally; see `docs/EMBEDDINGS.md`. Upgrading from the earlier nomic version: **back up**, `alembic upgrade head` (migration 0003 keeps old vectors in `embedding_legacy`), then `python -m scripts.reindex_embeddings`. Switching to another model later needs a new migration (the vector size is fixed in the schema), `EMBEDDING_DIM`, and the model's own query-instruction rules.

## Calibrating `RETRIEVAL_THRESHOLD`
0.45 is an uncalibrated placeholder (originally for nomic-embed-text; NOT validated for BGE). Collect ~30 questions with known relevant papers plus ~10 off-topic ones; log top scores for each; pick a threshold separating them; revisit when the corpus or embedding model changes.

## Tests
```bash
python -m unittest discover -s tests/unit -t .     # works with stdlib only; framework tests skip if deps missing
pytest                                             # after pip install -r requirements.txt
```
`tests/fixtures/fakes.py` is test-only synthetic data. `tests/integration/` is empty: DB and live-Ollama tests still need to be written.

## Known limitations
- No OCR; no conversation history; no authentication; no rate limiting.
- Ingestion runs in-process via FastAPI BackgroundTasks (lost on restart; stuck `processing` rows are not auto-recovered).
- Section detection is heuristic (common heading names only).
- Evidence budget uses ~3 chars/token, an approximation.
- Qwen 2.5 3B may ignore the JSON/citation format; the service handles this (parse failure -> 502, missing citations -> warning) but quality needs evaluation on real papers.
- No retrieval-quality or claim-alignment evaluation harness yet.

## Integrating into the team repo
1. Copy `app/`, `migrations/`, `scripts/`, `alembic.ini`, `requirements.txt`, `.env.example`.
2. Merge dependencies; set `DATABASE_URL` to a Postgres with the `vector` extension.
3. Mount via `from app.main import create_app` or include the routers under `/api`.
4. `alembic upgrade head`, import the corpus, hit `/api/health` until `status: ok`.
