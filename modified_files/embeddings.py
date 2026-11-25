"""Layer 4: embeddings.

Tries sentence-transformers (best quality); if it is not installed or the
model cannot download, falls back to a dependency-free hashing embedder so
the knowledge base always works offline on your device.
"""
import hashlib
import re
from typing import List

from esg_kb import settings

_embedder = None

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


class HashingEmbedder:
    """Deterministic char-trigram hashing -> 384-dim normalised vector.
    Zero dependencies, works offline; good enough for keyword-style RAG."""

    dim = 384

    def embed(self, text: str) -> List[float]:
        import numpy as np

        vec = np.zeros(self.dim, dtype=np.float32)
        norm = " ".join(_TOKEN_RE.findall((text or "").lower()))
        padded = f"  {norm}  "
        for i in range(len(padded) - 2):
            gram = padded[i:i + 3]
            h = int(hashlib.md5(gram.encode()).hexdigest()[:8], 16)
            vec[h % self.dim] += 1.0
        n = float(np.linalg.norm(vec))
        if n > 0:
            vec /= n
        return vec.tolist()


class SentenceEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # lazy import

        self.model = SentenceTransformer(model_name)

    def embed(self, text: str) -> List[float]:
        return self.model.encode(text, normalize_embeddings=True).tolist()


def get_embedder():
    global _embedder
    if _embedder is None:
        try:
            _embedder = SentenceEmbedder(settings.EMBED_MODEL)
            print(f"[embed] using {settings.EMBED_MODEL}")
        except Exception as exc:
            print(f"[embed] sentence-transformers unavailable ({exc}); "
                  "using built-in hashing embedder.")
            _embedder = HashingEmbedder()
    return _embedder


def embed(text: str) -> List[float]:
    return get_embedder().embed(text)
