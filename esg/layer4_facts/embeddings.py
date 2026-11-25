"""Layer 4: embeddings (Qwen3-Embedding-0.6B / BGE-M3, offline fallback).

Fine-tune THE EMBEDDER (locally, on your hard negatives) - never the chatbot.
If sentence-transformers or the model is unavailable, a dependency-free
hashing embedder keeps the pipeline running so nothing blocks.

NEW (2026-11):
- norm() / cosine() helpers for retrieval
"""
import hashlib
import re

import numpy as np

from esg import settings

_embedder = None
_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


class HashingEmbedder:
    """Deterministic char-trigram hashing -> 384-dim unit vector. Zero deps."""
    dim = 384

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        padded = "  " + " ".join(_TOKEN_RE.findall((text or "").lower())) + "  "
        for i in range(len(padded) - 2):
            h = int(hashlib.md5(padded[i:i + 3].encode()).hexdigest()[:8], 16)
            vec[h % self.dim] += 1.0
        n = np.linalg.norm(vec)
        return vec / n if n > 0 else vec


class SentenceEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # lazy
        import torch

        self.model = SentenceTransformer(model_name, model_kwargs={"torch_dtype": torch.float32})

    def embed(self, text: str) -> np.ndarray:
        v = self.model.encode(text, normalize_embeddings=True)
        return np.asarray(v, dtype=np.float32)


def get_embedder():
    global _embedder
    if _embedder is None:
        try:
            _embedder = SentenceEmbedder(settings.EMBED_MODEL)
            print(f"[embed] {settings.EMBED_MODEL}")
        except Exception as exc:
            print(f"[embed] {settings.EMBED_MODEL} unavailable ({exc}); "
                  "using offline hashing embedder.")
            _embedder = HashingEmbedder()
    return _embedder


def embed(text: str) -> np.ndarray:
    return get_embedder().embed(text)


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


# ---- NEW: helpers ----

def norm(v: np.ndarray) -> float:
    """L2 norm of vector."""
    return float(np.linalg.norm(v))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    na = norm(a)
    nb = norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))