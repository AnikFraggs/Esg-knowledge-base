"""All configuration in one place. Environment variables override.

Model sizing (2026-11 update, see docs/MODEL_SIZING.md):
- MAIN LLM   : Qwen3-4B-Instruct-2507 Q4_K_M (~2.5 GB) - everything interactive
- HARD-CASE  : qwen3:8b (~5 GB) - flagged facts only, NEVER at the same time
               as the 4B (OLLAMA_MAX_LOADED_MODELS=1)
- Embeddings : Qwen3-Embedding-0.6B | Reranker: bge-reranker-v2-m3 (>=0.45 prefilter)
- OCR        : pdfplumber for digital pages; scanned -> PP-OCR (classic
               PaddleOCR) first, Tesseract fallback. Docling REMOVED - it hung
               40+ min/page on this laptop's CPU.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # downloaded originals (layer 1)
EXTRACTED_DIR = DATA_DIR / "extracted"  # raw extraction JSONL per filing (layer 2)
EXPORT_DIR = DATA_DIR / "exports"     # training JSONL for your LOCAL fine-tuning

WATCHLIST_FILE = ROOT / "config" / "watchlist.yaml"

_env_db = os.getenv("DATABASE_URL", "").strip()
# only accept well-formed SQLAlchemy URLs (sqlite:///..., postgresql+psycopg2://...)
DATABASE_URL = _env_db if "://" in _env_db else f"sqlite:///{(DATA_DIR / 'esg.db').as_posix()}"

# ---------------- Layer 2: extraction / OCR ----------------
PDF_TEXT_CHARS_PER_PAGE = 40     # fewer -> treat page as scanned -> OCR
OCR_DPI = 200                    # full-page OCR rasterization
OCR_REGION_DPI = 300             # region-selection crops (small box, afford more)
OCR_LANG = "eng"
# scanned-page OCR order: PP-OCR (classic PaddleOCR, CPU friendly) first,
# Tesseract fallback. Optional VLM hook for hard pages/tables: set
# OCR_VLM_MODEL to an Ollama vision model (e.g. a PaddleOCR-VL / GLM-OCR
# served locally) - "" disables it.
OCR_VLM_MODEL = os.getenv("ESG_OCR_VLM_MODEL", "")

# word-level boxes for evidence highlighting (only evidence pages are stored)
WORD_BOX_MAX_PAGES = 200         # per filing
WORD_BOX_MAX_PER_PAGE = 800

# source-of-truth confidence per extraction path (guidance #4: XBRL primary)
CONFIDENCE = {
    "xbrl": 0.95,
    "xlsx": 0.80,
    "pdf_text": 0.60,
    "pdf_ocr": 0.40,
    "manual_selection": 0.97,   # a human drew the box; high confidence,
                                # skips the review queue by design
}
LOW_CONFIDENCE_REVIEW = 0.45      # below -> human review queue (auto facts only)

# ---------------- LLM (explains discrepancies ONLY, code finds them) ----------------
LLM_PROVIDER = os.getenv("ESG_LLM_PROVIDER", "local")  # local | openai_compatible
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# one heavy model at a time: 4B for everything interactive, 8B only when the
# 4B and the deterministic checks disagree (never loaded simultaneously)
LLM_MODEL_MAIN = os.getenv("ESG_LLM_MODEL_MAIN", "qwen3:4b-instruct-2507")
LLM_MODEL_HARD = os.getenv("ESG_LLM_MODEL_HARD", "qwen3:8b")
OPENAI_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
OPENAI_MODEL = os.getenv("VLLM_MODEL", "qwen3:4b-instruct-2507")
LLM_TEMPERATURE = 0.0
LLM_TIMEOUT = 240
# CPU-friendly Ollama options (docs/MODEL_SIZING.md): short context, short
# keep_alive, no thinking mode, 6-10 threads (benchmark 6 vs 8 vs 10)
LLM_NUM_CTX = int(os.getenv("ESG_LLM_NUM_CTX", "4096"))
LLM_NUM_THREAD = int(os.getenv("ESG_LLM_NUM_THREAD", "8"))
LLM_KEEP_ALIVE = os.getenv("ESG_LLM_KEEP_ALIVE", "2m")
LLM_NO_THINK = os.getenv("ESG_LLM_NO_THINK", "1") == "1"  # /no_think for hybrids

# ---------------- Layer 4: dedup (matching problem: embed -> rerank -> signature) ----
EMBED_MODEL = os.getenv("ESG_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
RERANKER_MODEL = os.getenv("ESG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
DUP_EMBED_CANDIDATES = 10         # candidates pulled by embeddings
RERANKER_PREFILTER = 0.45         # embedding cosine below this -> never rerank
                                    # (keeps CPU cost low on the laptop)
DUP_RERANK_THRESHOLD = 0.85       # cross-encoder score needed to call it duplicate
# signature fields that must AGREE on top of the rerank score:
DUP_REQUIRED_FIELDS = ("metric_key", "period_label", "scope", "unit")

# ---------------- Layer 4: deterministic checks ----------------
CROSS_SOURCE_TOLERANCE = 0.005    # PDF vs XLSX vs XBRL: 0.5% relative tolerance
YOY_SANITY_FACTOR = 20.0          # value jumped > 20x YoY -> discrepancy
SCALE_SUSPICION_RATIO = 100.0     # ~1000x between sources -> millions vs thousands bug

# ---------------- Brain: answer confidence (computed, never self-reported) ----
# answer confidence = weighted mix of retrieval strength, citation verification
# and fact quality. Below ANSWER_CONFIDENCE_THRESHOLD the brain says
# "not found in database" instead of guessing. Calibrate the threshold on the
# eval set (esg/eval_set.py run_eval()) - 0.55 is a sane starting point.
ANSWER_CONFIDENCE_THRESHOLD = float(os.getenv("ESG_ANSWER_THRESHOLD", "0.55"))
CONFIDENCE_WEIGHTS = {
    "retrieval": 0.35,            # embedding similarity + reranker score
    "citation": 0.30,             # verify_evidence() found the quote verbatim
    "fact_quality": 0.35,         # fact confidence, source credibility, is_current,
                                  # approval status, multi-source agreement
}
RETRIEVAL_TOP_K = 5               # facts cited per answer
SOURCE_CREDIBILITY = {            # filing credibility by source type
    "xbrl": 1.00,                 # XBRL is primary truth
    "human": 1.00,
    "xlsx": 0.95,
    "pdf": 0.90,
    "": 0.80,
}
EVAL_VALUE_TOLERANCE = 0.005      # eval: |got - expected| / expected <= 0.5%


def ensure_dirs():
    for p in (DATA_DIR, RAW_DIR, EXTRACTED_DIR, EXPORT_DIR):
        p.mkdir(parents=True, exist_ok=True)
