"""All configuration in one place. Environment variables override."""
import os
from pathlib import Path

# Enforce offline huggingface operation to prevent network hang and rate-limiting
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # downloaded originals (layer 1)
EXTRACTED_DIR = DATA_DIR / "extracted"  # raw extraction JSONL per filing (layer 2)
EXPORT_DIR = DATA_DIR / "exports"     # training JSONL for your LOCAL fine-tuning
UPLOADS_DIR = ROOT / "uploads"        # user-uploaded documents (UI drag-and-drop)

WATCHLIST_FILE = ROOT / "config" / "watchlist.yaml"

_env_db = os.getenv("DATABASE_URL", "").strip()
# only accept well-formed SQLAlchemy URLs (sqlite:///..., postgresql+psycopg2://...)
DATABASE_URL = _env_db if "://" in _env_db else f"sqlite:///{(DATA_DIR / 'esg.db').as_posix()}"

# ---------------- Model roster (laptop CPU inference) ----------------
# 8B local model (qwen3:8b), 0.6B embedder, reranker with prefilter
LLM_MODEL_MAIN = os.getenv("ESG_LLM_MODEL_MAIN", "qwen3:8b")
LLM_MODEL_HARD = os.getenv("ESG_LLM_MODEL_HARD", "qwen3:8b")
EMBED_MODEL = os.getenv("ESG_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
RERANKER_MODEL = os.getenv("ESG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
RERANKER_PREFILTER = float(os.getenv("ESG_RERANKER_PREFILTER", "0.45"))

# Ollama CPU options (sent with every request by esg/llm.py)
LLM_NUM_CTX = int(os.getenv("ESG_LLM_NUM_CTX", "4096"))
LLM_NUM_THREAD = int(os.getenv("ESG_LLM_NUM_THREAD", "8"))
LLM_KEEP_ALIVE = os.getenv("ESG_LLM_KEEP_ALIVE", "2m")
LLM_NO_THINK = os.getenv("ESG_LLM_NO_THINK", "1")  # disable thinking mode

# OCR settings
PDF_TEXT_CHARS_PER_PAGE = int(os.getenv("ESG_PDF_TEXT_CHARS", "40"))
OCR_CHAR_THRESHOLD_PER_PAGE = PDF_TEXT_CHARS_PER_PAGE
OCR_DPI = int(os.getenv("ESG_OCR_DPI", "200"))
OCR_REGION_DPI = int(os.getenv("ESG_OCR_REGION_DPI", "300"))
OCR_LANG = os.getenv("ESG_OCR_LANG", "eng")
OCR_VLM_MODEL = os.getenv("ESG_OCR_VLM_MODEL", "")  # e.g., "paddleocr-vl:1.6" for table crops
WORD_BOX_MAX_PER_PAGE = int(os.getenv("ESG_WORD_BOX_MAX_PER_PAGE", "300"))
WORD_BOX_MAX_PAGES = int(os.getenv("ESG_WORD_BOX_MAX_PAGES", "20"))
EVAL_VALUE_TOLERANCE = 0.005

# Confidence thresholds
CONFIDENCE = {
    "xbrl": 0.95,
    "xlsx": 0.90,
    "pdf_table": 0.85,
    "pdf_text": 0.70,
    "ocr": 0.50,
    "manual_selection": 0.97,
}
LOW_CONFIDENCE_REVIEW = 0.45      # below -> human review queue

# Answer confidence weights and threshold
ANSWER_CONFIDENCE_THRESHOLD = float(os.getenv("ESG_ANSWER_CONFIDENCE_THRESHOLD", "0.55"))
ANSWER_CONFIDENCE_WEIGHTS = {
    "retrieval": 0.35,
    "citation_verified": 0.30,
    "fact_quality": 0.35,
}

# Source credibility for fact_quality computation
SOURCE_CREDIBILITY = {
    "xbrl": 0.95,
    "xlsx": 0.90,
    "pdf_table": 0.85,
    "pdf_text": 0.70,
    "ocr": 0.50,
    "manual_selection": 0.97,
}

# Word box caps
WORD_BOXES_PER_FILING = int(os.getenv("ESG_WORD_BOXES_PER_FILING", "50000"))

# Layer 4: dedup (matching problem: embed -> rerank -> signature)
DUP_EMBED_CANDIDATES = int(os.getenv("ESG_DUP_EMBED_CANDIDATES", "10"))
DUP_RERANK_THRESHOLD = float(os.getenv("ESG_DUP_RERANK_THRESHOLD", "0.85"))
DUP_REQUIRED_FIELDS = ("metric_key", "period_label", "scope", "unit")

# Layer 4: deterministic checks
CROSS_SOURCE_TOLERANCE = float(os.getenv("ESG_CROSS_SOURCE_TOLERANCE", "0.005"))
YOY_SANITY_FACTOR = float(os.getenv("ESG_YOY_SANITY_FACTOR", "20.0"))
SCALE_SUSPICION_RATIO = float(os.getenv("ESG_SCALE_SUSPICION_RATIO", "100.0"))

# LLM provider (for discrepancy explanations only)
LLM_PROVIDER = os.getenv("ESG_LLM_PROVIDER", "local")  # local | openai_compatible
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OPENAI_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
OPENAI_MODEL = os.getenv("VLLM_MODEL", "qwen3:8b")
LLM_TEMPERATURE = 0.0
LLM_TIMEOUT = 240


def ensure_dirs():
    for p in (DATA_DIR, RAW_DIR, EXTRACTED_DIR, EXPORT_DIR, UPLOADS_DIR):
        p.mkdir(parents=True, exist_ok=True)


# Export a settings object for easy imports
class Settings:
    def __init__(self):
        for k, v in globals().items():
            if k.isupper() and not k.startswith('_'):
                setattr(self, k, v)

settings = Settings()