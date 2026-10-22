"""Re-embed EXISTING chunks with the configured embedding model (BAAI/bge-large-en-v1.5).

  python -m scripts.reindex_embeddings --status          # counts only, no model load
  python -m scripts.reindex_embeddings                    # re-embed all pending chunks (resumable)
  python -m scripts.reindex_embeddings --batch-size 32 --max-batches 5

Reads chunk text from the database, writes new vectors + model/dim metadata, commits per batch. Safe to Ctrl+C and
re-run. Chunks that fail stay pending. Does not re-extract PDFs and cannot create duplicate chunks.
"""
import argparse

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.repositories.chunk_repo import SqlChunkRepo
from app.db.session import get_session_factory
from app.main import build_embedder
from app.services.reindex_service import ReindexService


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--max-batches", type=int)
    a = ap.parse_args()
    st = get_settings()
    setup_logging(st.log_level)
    repo = SqlChunkRepo(get_session_factory(), st.embedding_model, st.embedding_dim)
    print(f"model={st.embedding_model} dim={st.embedding_dim} total_chunks={repo.count_total()} "
          f"pending={repo.count_pending()}")
    print("embedding_model breakdown:", repo.model_breakdown())
    if a.status:
        return
    emb = build_embedder(st)
    emb.load()
    print(f"model loaded on {emb.device}")
    svc = ReindexService(repo, emb, a.batch_size,
                         lambda r: print(f"  batch {r.batches}: updated={r.updated} failed={len(r.failed_ids)}"))
    rep = svc.run(a.max_batches)
    print(f"done: updated={rep.updated} failed={len(rep.failed_ids)} still_pending={repo.count_pending()}")
    if rep.failed_ids:
        print("failed chunk ids (left pending):", ", ".join(rep.failed_ids[:50]))


if __name__ == "__main__":
    main()
