"""Layer 1: raw file storage.

Local filesystem backend for now. To move to MinIO/S3 later, implement the
same two functions against boto3 - nothing else in the pipeline changes.
Every stored file gets a sha256 checksum used as filing identity.
Different reports never overwrite each other: same name + different content
gets an _2, _3... suffix; identical content is stored once.
"""
import hashlib
import re
import shutil
from pathlib import Path

from esg import settings


def checksum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name).strip() or "report"
    return name[:120]


def store(company: str, local_file: Path, source_type: str,
          preferred_name: str = "") -> Path:
    """Copy a local file (downloaded or user-provided) into data/raw/<company>/.
    Returns the stored path. source_type: pdf | xbrl | xlsx."""
    settings.ensure_dirs()
    dest_dir = settings.RAW_DIR / company.lower().replace(" ", "_")
    dest_dir.mkdir(parents=True, exist_ok=True)

    src = Path(local_file)
    suffix = src.suffix.lower() or f".{source_type}"
    name = _safe_name(preferred_name or src.name)
    if Path(name).suffix.lower() not in (".pdf", ".xbrl", ".xml", ".xlsx", ".xlsm"):
        name = f"{Path(name).stem}{suffix}"
    dest = dest_dir / name

    if src.resolve() == dest.resolve():
        return dest

    # same name, different bytes -> never overwrite, pick a free suffix
    n = 2
    while dest.exists() and checksum(dest) != checksum(src):
        dest = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest


def store_upload(local_file: Path, preferred_name: str = "") -> Path:
    """Store an uploaded report directly into the uploads/ directory with checksumming."""
    settings.ensure_dirs()
    dest_dir = settings.UPLOADS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    src = Path(local_file)
    name = _safe_name(preferred_name or src.name)
    dest = dest_dir / name

    if src.resolve() == dest.resolve():
        return dest

    n = 2
    while dest.exists() and checksum(dest) != checksum(src):
        dest = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest
