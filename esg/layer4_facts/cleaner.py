"""Layer 2: text normalisation (ligatures, unicode, page numbers, spacing)."""
import re

LIGATURES = {
    "\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl",
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u200b": "",
}

_PAGE_NO = re.compile(r"^\s*(?:page\s*)?\d{1,4}\s*(?:of\s*\d{1,4})?\s*$", re.IGNORECASE)


def clean(text: str) -> str:
    if not text:
        return ""
    for bad, good in LIGATURES.items():
        text = text.replace(bad, good)
    text = text.replace("\x00", " ")
    lines = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if not _PAGE_NO.match(ln)]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
