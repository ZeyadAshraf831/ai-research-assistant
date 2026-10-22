"""MOCKED Ollama tests (httpx.MockTransport). Skipped automatically if httpx is not installed."""
import asyncio
import json
import unittest

try:
    import httpx
    from app.services.providers.ollama import OllamaClient
except ImportError:
    httpx = None

from app.core.errors import LLMTimeout, LLMUnavailable, ModelNotInstalled


def client(handler):
    return OllamaClient("http://x", "qwen2.5:3b", 5, 0.1, 4096, 700, httpx.MockTransport(handler))


@unittest.skipIf(httpx is None, "httpx not installed")
class OllamaMockedTests(unittest.TestCase):
    def test_health_states(self):
        def down(req): raise httpx.ConnectError("refused")
        self.assertEqual(asyncio.run(client(down).health()), {"ollama": "unreachable", "chat_model": "unknown"})
        tags = lambda names: (lambda req: httpx.Response(200, json={"models": [{"name": n} for n in names]}))
        self.assertEqual(asyncio.run(client(tags([])).health()), {"ollama": "up", "chat_model": "missing"})
        self.assertEqual(asyncio.run(client(tags(["qwen2.5:3b"])).health()), {"ollama": "up", "chat_model": "available"})

    def test_chat_ok_and_payload(self):
        seen = {}
        def h(req):
            seen.update(json.loads(req.content))
            return httpx.Response(200, json={"message": {"content": '{"answer":"x"}'}})
        self.assertEqual(asyncio.run(client(h).chat([{"role": "user", "content": "q"}])), '{"answer":"x"}')
        self.assertEqual((seen["model"], seen["stream"], seen["options"]["num_ctx"]), ("qwen2.5:3b", False, 4096))

    def test_chat_errors(self):
        def refused(req): raise httpx.ConnectError("x")
        def slow(req): raise httpx.ReadTimeout("x")
        missing = lambda req: httpx.Response(404, json={"error": "model not found"})
        for h, exc in ((refused, LLMUnavailable), (slow, LLMTimeout), (missing, ModelNotInstalled)):
            with self.assertRaises(exc):
                asyncio.run(client(h).chat([]))


if __name__ == "__main__":
    unittest.main()
