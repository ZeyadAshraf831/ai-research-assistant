"""SQL access for (re-)embedding existing chunks. A chunk is 'pending' when it has no embedding, or its
embedding_model / embedding_dim differ from the configured ones. UNVERIFIED against a live database."""
from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DatabaseError
from app.db.models import Chunk


class SqlChunkRepo:
    def __init__(self, session_factory, model: str, dim: int):
        self.sf, self.model, self.dim = session_factory, model, dim

    def _pending(self):
        return or_(Chunk.embedding.is_(None), Chunk.embedding_model.is_distinct_from(self.model),
                   Chunk.embedding_dim.is_distinct_from(self.dim))

    def count_pending(self) -> int:
        try:
            with self.sf() as s:
                return s.scalar(select(func.count()).select_from(Chunk).where(self._pending())) or 0
        except SQLAlchemyError:
            raise DatabaseError() from None

    def count_total(self) -> int:
        try:
            with self.sf() as s:
                return s.scalar(select(func.count()).select_from(Chunk)) or 0
        except SQLAlchemyError:
            raise DatabaseError() from None

    def model_breakdown(self) -> list[tuple[str | None, int]]:
        try:
            with self.sf() as s:
                return [(m, n) for m, n in s.execute(
                    select(Chunk.embedding_model, func.count()).group_by(Chunk.embedding_model))]
        except SQLAlchemyError:
            raise DatabaseError() from None

    def fetch_pending(self, after_id: str | None, limit: int) -> list[tuple[str, str]]:
        """Keyset pagination by id, so an interrupted run simply resumes with whatever is still pending."""
        q = select(Chunk.id, Chunk.text).where(self._pending()).order_by(Chunk.id).limit(limit)
        if after_id:
            q = q.where(Chunk.id > uuid.UUID(after_id))
        try:
            with self.sf() as s:
                return [(str(i), t) for i, t in s.execute(q)]
        except SQLAlchemyError:
            raise DatabaseError() from None

    def save(self, items: list[tuple[str, list[float]]]) -> None:
        if not items:
            return
        rows = [{"id": uuid.UUID(i), "embedding": v, "embedding_model": self.model, "embedding_dim": len(v)}
                for i, v in items]
        try:
            with self.sf() as s:
                s.execute(update(Chunk), rows)  # bulk UPDATE by primary key; one commit per batch
                s.commit()
        except SQLAlchemyError:
            raise DatabaseError() from None
