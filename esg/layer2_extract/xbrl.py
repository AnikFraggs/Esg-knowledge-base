"""Layer 2: XBRL extraction - the PRIMARY source of truth (guidance #4).

Priority:
1. Arelle (pip install arelle) - proper fact model: concept, context period,
   entity, and it VALIDATES the instance.
2. lxml generic scan - last-resort fallback, facts only, no validation.

Every fact keeps its XBRL tag as provenance.
"""
from esg import settings


def _extract_with_arelle(path: str):
    try:
        from arelle import Cntlr  # type: ignore
    except ImportError:
        return None
    try:
        cntlr = Cntlr.Cntlr(logFileName="logToPrint")
        model = cntlr.modelManager.load(str(path))
        items = []
        for fact in model.facts:
            concept = getattr(getattr(fact, "concept", None), "qname", None)
            name = getattr(concept, "localName", None) or str(
                getattr(fact, "qname", ""))
            if not name or fact.value is None:
                continue
            ctx = getattr(fact, "context", None)
            start = getattr(ctx, "start", None)
            end = getattr(ctx, "end", None) or getattr(ctx, "instant", None)
            scope = "standalone" if getattr(ctx, "scenario", None) is None else ""
            items.append({
                "kind": "fact",
                "metric_name": name,
                "value_text": str(fact.value),
                "period_start": start.isoformat() if hasattr(start, "isoformat") else "",
                "period_end": end.isoformat() if hasattr(end, "isoformat") else "",
                "period_label": "",
                "scope": scope,
                "source_ref": f"xbrl:{name}",
                "quote": f"{name} = {fact.value}",
                "confidence": settings.CONFIDENCE["xbrl"],
                "engine": "arelle",
                "tables": [],
            })
        if items:
            return items
    except Exception as exc:
        print(f"[xbrl] arelle failed ({exc}); falling back to lxml scan")
    return None


def _extract_with_lxml(path: str):
    from lxml import etree

    tree = etree.parse(str(path))
    items = []
    for el in tree.getroot().iter():
        if not isinstance(el.tag, str):
            continue
        tag = etree.QName(el).localname
        if tag in ("context", "unit", "entity", "identifier", "period",
                   "startDate", "endDate", "instant", "segment", "scenario",
                   "measure"):
            continue
        text = (el.text or "").strip()
        if not text or len(text) > 2000:
            continue
        pretty = "".join((" " if ch.isupper() and i else "") + ch
                         for i, ch in enumerate(tag))
        items.append({
            "kind": "fact",
            "metric_name": pretty.strip(),
            "value_text": text,
            "period_start": "", "period_end": "", "period_label": "",
            "scope": "",
            "source_ref": f"xbrl:{tag}",
            "quote": f"{tag} = {text}",
            "confidence": 0.85,  # fallback parser, slightly below arelle
            "engine": "lxml",
            "tables": [],
        })
    return items


def extract(path: str) -> list:
    items = _extract_with_arelle(path) or _extract_with_lxml(path)
    print(f"[xbrl] -> {len(items)} facts")
    return items
