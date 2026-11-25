"""Layer 1: download a report URL. Only PDF / XBRL / XLSX pass (by design)."""
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from esg import settings
from esg.layer1_ingest import storage

SUPPORTED_EXTS = (".pdf", ".xbrl", ".xml", ".xlsx", ".xlsm")
MAX_MB = 200
TIMEOUT = 60
USER_AGENT = "esg-pipeline/0.2 (research project)"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", (text or "").lower()).strip("_") or "company"


def guess_ext(url: str, headers: dict) -> str:
    path = unquote(urlparse(url).path).lower()
    for ext in SUPPORTED_EXTS:
        if path.endswith(ext):
            return ext
    ctype = (headers.get("Content-Type") or "").lower()
    if "pdf" in ctype:
        return ".pdf"
    if "excel" in ctype or "spreadsheet" in ctype:
        return ".xlsx"
    if "xml" in ctype:
        return ".xml"
    return ""


def download(url: str, company: str) -> Path:
    """Stream a report into raw storage and return the stored path."""
    settings.ensure_dirs()
    tmp_dir = settings.RAW_DIR / "_downloads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"dl_{int(time.time() * 1000)}"

    with requests.get(url, headers={"User-Agent": USER_AGENT},
                      timeout=TIMEOUT, stream=True) as r:
        r.raise_for_status()
        ext = guess_ext(url, dict(r.headers))
        if ext not in SUPPORTED_EXTS:
            raise ValueError(
                f"Unsupported file type for {url} (ext/content-type = {ext!r}). "
                "Only PDF and XBRL/XLSX are accepted."
            )
        size = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                size += len(chunk)
                if size > MAX_MB * 1024 * 1024:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    raise ValueError(f"Download exceeds {MAX_MB} MB limit.")
                f.write(chunk)

    # keep the URL's file name when it has one (e.g. DLF_BRSR_2024-25.pdf)
    url_name = Path(unquote(urlparse(url).path)).name
    preferred = url_name if Path(url_name).suffix else f"report{ext}"
    stored = storage.store(company, tmp, ext.lstrip("."), preferred_name=preferred)
    tmp.unlink(missing_ok=True)
    return stored
