"""Layer 3: keyword utilities used by both dedup checks and the rule-based
fallback extractor. Pure Python, no external NLP dependency."""
import re
from typing import Set

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "when", "of",
    "to", "in", "on", "for", "with", "without", "by", "at", "from", "as", "is",
    "are", "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "will", "would", "shall", "should", "can", "could", "may",
    "might", "must", "not", "no", "nor", "so", "than", "that", "this", "these",
    "those", "it", "its", "their", "there", "here", "he", "she", "they", "we",
    "you", "i", "which", "who", "whom", "what", "where", "why", "how", "all",
    "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "only", "own", "same", "too", "very", "just", "also", "per", "into",
    "during", "before", "after", "above", "below", "up", "down", "out", "off",
    "over", "under", "again", "further", "once", "about", "between", "under",
    "whether", "please", "kindly", "details", "detail", "provide", "given",
    "following", "above", "page", "report", "annexure", "sr", "no", "yes",
}

_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")


def tokenize(text: str):
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in STOPWORDS]


def keyword_set(text: str) -> Set[str]:
    return set(tokenize(text))


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Similarity of two keyword sets: |A and B| / |A or B|."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def new_keyword_ratio(new: Set[str], old: Set[str]) -> float:
    """Share of keywords in `new` that do NOT appear in `old`."""
    if not new:
        return 0.0
    return len(new - old) / len(new)
