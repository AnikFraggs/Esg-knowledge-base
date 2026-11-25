"""Layer 4: cross-encoder reranker (optional).

Base model by default (bge-reranker-v2-m3 or Qwen3-Reranker-0.6B); you
fine-tune it LOCALLY on the exported hard negatives (data/exports/).
If sentence-transformers or the model is missing, reranking is skipped and
the embedding similarity is used as the score - the pipeline never breaks.
"""
import numpy as np

from esg import settings

_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        try:
            from sentence_transformers import CrossEncoder  # lazy
            import torch

            _reranker = CrossEncoder(settings.RERANKER_MODEL, model_kwargs={"torch_dtype": torch.float32})
            print(f"[rerank] {settings.RERANKER_MODEL} (float32)")
        except Exception as exc:
            print(f"[rerank] {settings.RERANKER_MODEL} unavailable ({exc}); "
                  "skipping rerank (embedding similarity only).")
            _reranker = False
    return _reranker


def rerank(question_or_pairs, candidates: list | None = None):
    """
    Polymorphic reranker:
    - rerank(question, candidates): candidates = [{'id', 'text', 'score'}, ...] -> sorted list
    - rerank(pairs): pairs = [(q, text), ...] -> list of float scores
    """
    model = _get_reranker()
    if model is False:
        return None if candidates is not None else []

    if candidates is None and isinstance(question_or_pairs, list):
        pairs = question_or_pairs
        if not pairs:
            return []
        try:
            scores = model.predict(pairs, batch_size=32)
            return [float(np.asarray(s).reshape(-1)[0]) for s in scores]
        except Exception as exc:
            print(f"[rerank] pairs failed ({exc})")
            return [0.5] * len(pairs)
    else:
        question = str(question_or_pairs)
        if not candidates:
            return None
        try:
            pairs = [(question, c["text"]) for c in candidates]
            scores = model.predict(pairs, batch_size=32)
            for c, s in zip(candidates, scores):
                c["rerank_score"] = float(np.asarray(s).reshape(-1)[0])
            return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        except Exception as exc:
            print(f"[rerank] failed ({exc}); using embedding scores.")
            return None
