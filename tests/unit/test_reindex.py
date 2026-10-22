"""Re-index logic tests with an in-memory repo (SYNTHETIC). Real SQL is covered only by tests/integration."""
import unittest

from app.core.errors import EmbeddingUnavailable
from app.services.providers.embeddings import HuggingFaceEmbedder
from app.services.reindex_service import ReindexService
from tests.fixtures.fakes import FakeChunkRepo, FakeSTModel


def emb(model=None):
    m = model or FakeSTModel()
    return HuggingFaceEmbedder("BAAI/bge-large-en-v1.5", 1024, model_loader=lambda *a: m,
                               cuda_available=lambda: False, batch_size=4), m


TEXTS = {f"id{i:02d}": f"chunk text number {i} about topic{i}" for i in range(10)}


class ReindexTests(unittest.TestCase):
    def test_updates_all_pending_without_duplicating(self):
        repo = FakeChunkRepo(dict(TEXTS))
        e, _ = emb()
        rep = ReindexService(repo, e, batch_size=4).run()
        self.assertEqual((rep.pending_at_start, rep.updated, rep.failed_ids, rep.batches), (10, 10, [], 3))
        self.assertEqual(len(repo.rows), 10)  # no new rows
        self.assertEqual(repo.count_pending(), 0)
        self.assertTrue(all(r["model"] == "BAAI/bge-large-en-v1.5" and len(r["vec"]) == 1024 for r in repo.rows.values()))

    def test_resume_after_interruption_skips_done_chunks(self):
        repo = FakeChunkRepo(dict(TEXTS))
        e, m = emb()
        ReindexService(repo, e, batch_size=4).run(max_batches=1)  # "interrupted" after 4 chunks
        self.assertEqual(repo.count_pending(), 6)
        m.calls.clear()
        rep = ReindexService(repo, e, batch_size=4).run()
        embedded = sum(len(c[0]) for c in m.calls)
        self.assertEqual((rep.pending_at_start, rep.updated, embedded), (6, 6, 6))  # only the remaining ones
        self.assertEqual(repo.count_pending(), 0)

    def test_already_current_chunks_untouched(self):
        repo = FakeChunkRepo(dict(TEXTS), done={"id00", "id01"})
        e, _ = emb()
        rep = ReindexService(repo, e).run()
        self.assertEqual(rep.updated, 8)
        self.assertEqual(repo.rows["id00"]["vec"], [0.0])

    def test_failed_chunk_stays_pending_others_succeed(self):
        texts = dict(TEXTS)
        texts["id03"] = "   "  # invalid text
        repo = FakeChunkRepo(texts)
        e, _ = emb()
        rep = ReindexService(repo, e, batch_size=4).run()
        self.assertEqual(rep.failed_ids, ["id03"])
        self.assertEqual(rep.updated, 9)
        self.assertIsNone(repo.rows["id03"]["vec"])
        self.assertEqual(repo.count_pending(), 1)

    def test_infrastructure_failure_aborts_but_keeps_completed_batches(self):
        repo = FakeChunkRepo(dict(TEXTS))
        e, m = emb()
        svc = ReindexService(repo, e, batch_size=4)
        svc.run(max_batches=1)
        m.mode = "raise"
        with self.assertRaises(EmbeddingUnavailable):
            ReindexService(repo, e, batch_size=4).run()
        self.assertEqual(repo.count_pending(), 6)  # nothing falsely marked indexed
        m.mode = "ok"
        ReindexService(repo, e, batch_size=4).run()
        self.assertEqual(repo.count_pending(), 0)

    def test_nan_vector_batch_not_marked_indexed(self):
        repo = FakeChunkRepo({"a": "alpha text one", "b": "beta text two"})
        e, m = emb(FakeSTModel(mode="nan"))
        rep = ReindexService(repo, e).run()
        # batch fails validation -> item-by-item: the model emits NaN for every single-text call too
        self.assertEqual(sorted(rep.failed_ids), ["a", "b"])
        self.assertEqual(repo.count_pending(), 2)

    def test_empty_corpus(self):
        rep = ReindexService(FakeChunkRepo({}), emb()[0]).run()
        self.assertEqual((rep.pending_at_start, rep.updated, rep.batches), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
