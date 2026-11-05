"""rag.embed: optional embedding endpoint with a local fallback."""
import unittest
from unittest import mock

import requests

import rag


class R:
    def __init__(self, vecs):
        self.vecs = vecs

    def raise_for_status(self):
        pass

    def json(self):
        return {"embeddings": self.vecs}


class Embed(unittest.TestCase):
    def test_uses_embedding_endpoint_then_falls_back(self):
        calls = []

        def post(url, **kw):
            calls.append(url)
            if url.startswith(rag.EMBED_URL):
                raise requests.ConnectionError("embedding_endpoint down")
            return R([[3.0, 4.0]])
        with (mock.patch.object(rag, "EMBED_URL", "http://embedding.example.com"),
              mock.patch.object(rag.requests, "post", post)):
            v = rag.embed(["x"])
            self.assertEqual(calls, [f"{rag.EMBED_URL}/api/embed", f"{rag.OLLAMA_URL}/api/embed"])
        self.assertAlmostEqual(float(v[0][0]), 0.6, places=5)

    def test_embedding_endpoint_answers(self):
        with mock.patch.object(rag.requests, "post", lambda url, **kw: R([[1.0, 0.0]])) as _:
            self.assertEqual(rag.embed(["x"]).shape, (1, 2))

    def test_both_down_raises(self):
        def post(url, **kw):
            raise requests.ConnectionError("down")
        with mock.patch.object(rag.requests, "post", post):
            with self.assertRaises(requests.ConnectionError):
                rag.embed(["x"])
