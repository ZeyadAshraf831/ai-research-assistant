# Supabase Integration Guide

Sources checked: official Supabase docs (connection guide, Row Level Security, Securing your API, JWT guide) on the date this was written. Re-check them before production; Supabase changes dashboards and defaults.

## 1. What the project actually needs from Supabase

| Need | Required now? | How |
|---|---|---|
| PostgreSQL database with pgvector | **Yes** | `DATABASE_URL` (SQLAlchemy) |
| Supabase URL / publishable key / secret key | **No** (code never reads them) | Reserved for Storage/Auth work |
| Supabase Storage | No (❌ not implemented) | PDFs stay on local disk |
| Supabase Auth | No (❌ not implemented) | See `SECURITY.md` |

## 2. Dashboard steps (do these once)

1. Open your project → **Database → Extensions**, search `vector`, enable it. (Alternatively the migration runs `CREATE EXTENSION IF NOT EXISTS vector`; if it errors or the `vector` type is not found, enable it in the dashboard first.) 🟡
2. **Project Settings → Database**: if you do not know the database password, **reset it** there (you cannot read it back). Store it in a password manager.
3. Click **Connect** at the top of the dashboard and copy the connection strings (Direct, Session pooler, Transaction pooler).
4. **Project Settings → API Keys**: note the publishable key (`sb_publishable_…`) and secret key (`sb_secret_…`). You do **not** need them to run the backend as it is today.
5. **Data API hardening (recommended)**: since this backend uses a direct Postgres connection and no Supabase client, you can turn **Enable Data API** off (Settings → API / Data API) so the tables are not reachable over REST at all. Migration `0002` also enables RLS as a second layer.
6. After the first migration, check **Table Editor**: `papers`, `chunks`, `ingestion_jobs` should show RLS **enabled**.

## 3. Choosing the connection string

| Mode | Host:port | Use when |
|---|---|---|
| Direct | `db.<ref>.supabase.co:5432` | Migrations and a long-lived backend, **if your network has IPv6** (the docs state direct connections are IPv6-only unless you buy the IPv4 add-on) |
| Session pooler | `aws-…pooler.supabase.com:5432`, user `postgres.<ref>` | Long-lived backend on an **IPv4-only network** (many home/office networks, many hosts). Supports prepared statements |
| Transaction pooler | `…:6543` | Serverless/short-lived clients. **No prepared statements** → set `DB_DISABLE_PREPARED_STATEMENTS=true` |

Recommendation for this backend (a persistent FastAPI process): **direct if IPv6 works, otherwise session pooler.** Run Alembic migrations over direct or session mode, not transaction mode.

Format for this project (driver prefix matters; `sslmode=require` is recommended for Supabase):
```
postgresql+psycopg://postgres.<ref>:<URL-ENCODED-PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```
Use the exact host from the dashboard's Connect panel; the host/user format above is illustrative and varies by project. Password characters like `@ # / :` must be percent-encoded.

SQLAlchemy pooling: `DB_POOL_SIZE` (default 5) + `DB_MAX_OVERFLOW` (default 5) bound the connections this app opens; keep the total well below your Supabase plan's connection limit. 🟡

## 4. Schema (matches `app/db/models.py` and migrations `0001`, `0002`, `0003`)

### `papers` — one row per PDF
PK `id uuid`. Columns: `title text` (required), `authors jsonb` (list of strings), `abstract`, `publication_year int` (indexed), `doi varchar(255)` **unique**, `source_url`, `storage_key` (file name on local disk), `content_hash varchar(64)` **unique** (SHA-256 of the PDF; the idempotency key), `status` (`pending|processing|indexed|failed|needs_ocr`, indexed), `error_message`, `created_at`, `updated_at`.

### `chunks` — searchable text
PK `id uuid`; FK `paper_id → papers.id ON DELETE CASCADE` (indexed); `text`, `page_number int NULL` (**NULL = unknown, never guessed**), `section varchar(100)`, `chunk_index int` (`UNIQUE(paper_id, chunk_index)`), `embedding vector(1024) NULL`, `embedding_model varchar(100) NULL` (indexed), `embedding_dim int NULL` (NULL together with `embedding` = pending re-index). After upgrading a database that already held 768-dim vectors, the old vectors remain in the unmapped column `embedding_legacy` (+ `embedding_legacy_model`), `created_at`. Index: `HNSW (embedding vector_cosine_ops)`.

### `ingestion_jobs` — processing log
PK `id uuid`; FK `paper_id → papers.id ON DELETE CASCADE`; `status` (`pending|running|succeeded|failed`), `error_message`, `created_at`, `updated_at`.

### Not implemented (❌ / 💡)
`collections`, `conversations`, `messages`: no code uses them. Add them only when a feature needs them (💡 `collections(id, name)` + `paper_collections(paper_id, collection_id)`; `conversations(id, user_id, created_at)` + `messages(id, conversation_id, role, content, citations jsonb, created_at)`), each with RLS enabled.

### Embedding model versioning and the vector size
The column is `vector(1024)` for `BAAI/bge-large-en-v1.5`. Search only compares vectors whose `embedding_model` equals `EMBEDDING_MODEL` and whose `embedding` is not NULL, so other models' vectors are never mixed in. Migration `0003_bge_1024.py` is **non-destructive**: on an empty `chunks` table it just creates the 1024 column; if chunks already exist it renames the old column to `embedding_legacy`, keeps all text/page/section/IDs, and marks every chunk as pending. Then run `python -m scripts.reindex_embeddings`. Old vectors are never truncated, padded or reinterpreted; dropping `embedding_legacy` is a manual step you do after verifying retrieval. **Back up (Supabase dashboard → Database → Backups, or `pg_dump`) before running `alembic upgrade head` on a live database.** See `EMBEDDINGS.md`. pgvector's HNSW index supports up to 2,000 dimensions for `vector`; filtering by `embedding_model` and `papers.status` is applied together with the ANN index, which can return fewer than `limit` rows on very selective filters (tunable with `hnsw.ef_search`, unverified here).

### Similarity search
No SQL function (RPC) is needed: SQLAlchemy emits `ORDER BY embedding <=> $1` directly. You only need a Supabase RPC if a client calls search through the Data API, which this design avoids on purpose.

## 5. Applying migrations
```powershell
alembic upgrade head      # 0001 schema, 0002 RLS, 0003 vector(1024) + HNSW cosine index
alembic current           # expect: 0003 (head)
```
Verify RLS and extension in the SQL Editor:
```sql
select extname from pg_extension where extname='vector';
select relname, relrowsecurity from pg_class where relname in ('papers','chunks','ingestion_jobs');
select rolname, rolbypassrls from pg_roles where rolname='postgres';
```
Also consider enabling RLS on `public.alembic_version` manually (`alter table public.alembic_version enable row level security;`).
RLS note: the backend connects as the table owner, which bypasses RLS unless `FORCE ROW LEVEL SECURITY` is set. **Do not enable FORCE**, or the backend loses access unless you write policies for its role.

## 6. Supabase client: when would it be justified?
| Capability | SQLAlchemy | Supabase Python client |
|---|---|---|
| pgvector `<=>` search with filters | ✅ native | Only via a custom SQL function (RPC) |
| Multi-statement transactions (replace chunks) | ✅ | ❌ no client-side transactions |
| Alembic migrations / existing models | ✅ | ❌ |
| Storage uploads, Auth admin | ❌ | ✅ |

So: keep SQLAlchemy for all data; add the client (or plain HTTPS) **only** for Storage/Auth if you implement them. Avoid reading the same tables through both.

## 7. Supabase Storage plan (❌ not implemented; 💡 design)
1. Create a **private** bucket `papers` (no public access); restrict MIME type to `application/pdf` and set a file-size limit.
2. Introduce a small `StorageBackend` interface (`put(key, bytes)`, `get(key) -> bytes`) with `LocalStorage` (today's code) and `SupabaseStorage` (server-side, secret key), selected by an env var. `papers.storage_key` already abstracts the location.
3. The backend downloads the PDF at `run_job()` time. No storage policies are needed for the backend because the secret key bypasses them; never give browsers the secret key. If browsers should upload directly, use signed upload URLs created by the backend.
Effort estimate: roughly 60–100 lines plus tests. Until then, if you deploy the backend somewhere with an ephemeral disk, uploaded PDFs are lost on restart (rows survive), which is a real limitation.

## 8. Verification matrix
| Check | Without Supabase | Needs real credentials |
|---|---|---|
| Chunking, evidence, citations, RAG logic (25 tests) | ✅ ran | |
| Settings validation, route tests, Ollama mocks | 🟡 after `pip install -r requirements.txt` | |
| Migrations, pgvector search, ingestion | 🟡 against local `docker compose up -d db` | Supabase connection |
| Pooler/IPv4/SSL behaviour, RLS state in dashboard | | ✅ required |
| Live Qwen (Ollama) / BGE model download (Hugging Face, ~1.3 GB) | needs Ollama / internet once (no Supabase) | |
