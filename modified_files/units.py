"""Layer 3: unit / scale / currency / sign normalization.

parse_value("INR 1,234.5 crore") ->
    {"value": 1234.5, "scale": "crore", "scale_factor": 1e7,
     "value_base": 1.2345e10, "unit": "INR", "currency": "INR", "sign": 1}

parse_value("(1,234) million") -> value -1234, scale million, factor 1e6 ...
"""
import re

# scale words -> factor (millions vs thousands is a classic hard-negative bug)
SCALES = {
    "thousand": 1e3, "thousands": 1e3, "000s": 1e3, "k": 1e3,
    "lakh": 1e5, "lakhs": 1e5,
    "million": 1e6, "millions": 1e6, "mn": 1e6, "m": 1e6,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "billion": 1e9, "billions": 1e9, "bn": 1e9,
}

UNIT_ALIASES = {
    "inr": "INR", "rs": "INR", "rupees": "INR", "rupiah": "IDR",
    "usd": "USD", "$": "USD", "eur": "EUR",
    "tonnes": "TONNES", "tonne": "TONNES", "t": "TONNES", "tons": "TONNES",
    "tco2e": "TONNES", "tco2": "TONNES", "co2e": "TONNES",
    "kt": "KILOTONNES", "kilotonnes": "KILOTONNES", "mt": "MILLION_TONNES",
    "kl": "KILOLITRES", "kilolitres": "KILOLITRES", "kilolitre": "KILOLITRES",
    "m3": "M3", "cubic metres": "M3", "cum": "M3",
    "gwh": "GWH", "mwh": "MWH", "kwh": "KWH",
    "%": "PCT", "percent": "PCT", "per cent": "PCT",
    "hectares": "HECTARES", "ha": "HECTARES",
    "gj": "GJ", "tj": "TJ", "mj": "MJ",
}

_NUM = re.compile(r"-?\(?\s*\d[\d,\.]*\s*\)?")
_SCALE_RE = re.compile(
    r"\b(thousands?|lakhs?|millions?|crores?|billions?|mn|bn|k|cr)\b", re.I)
_UNIT_RE = re.compile(
    r"(\$|₹|%|\b(?:inr|rs\.?|rupees?|usd|eur|tonnes?|tons?|tco2e?|co2e|kt|mt|kl|"
    r"kilolitres?|kilotonnes?|million tonnes|m3|cubic metres?|gwh|mwh|kwh|"
    r"percent|hectares?|ha|gj|tj|mj)\b)", re.I)

# numbers that are LABELS, not values: "Scope 1", "Principle 6", "FY 2024",
# "Part B", "S.No. 4", "Q2"...
_LABEL_BEFORE = re.compile(
    r"\b(?:scopes?|principles?\s+\w*|parts?|sections?|annexures?|chapters?|"
    r"clauses?|pages?|quarters?|fy|yrs?|years?|sl\.?\s*nos?\.?|sr\.?\s*nos?\.?|"
    r"nos?\.?|tables?|figures?|figs?|paras?|paragraphs?|indicators?|q)\s*\.?\s*$",
    re.I)
_YEARISH = re.compile(r"^(?:19|20)\d{2}$")


def _candidates(text: str) -> list:
    """All plausible number tokens, rejecting digits glued to letters
    (CO2e, ISO14001, FY24) and range tails (2024-25 -> the -25 part).

    Boundary checks use the CORE of the match (the regex also consumes
    surrounding spaces, so m.start()/m.end() would point at the wrong chars).
    """
    out = []
    for m in _NUM.finditer(text):
        g = m.group(0)
        core_s = m.start() + (len(g) - len(g.lstrip()))    # first non-space
        core_e = m.end() - (len(g) - len(g.rstrip()))      # last non-space
        core = text[core_s:core_e]
        if core_s > 0 and text[core_s - 1].isalnum():
            continue                      # CO2e / ISO14001 / FY24
        if core_e < len(text) and text[core_e].isalnum():
            continue                      # the 2 in CO2e, 5x ...
        if (core_s >= 2 and text[core_s - 1] == "-"
                and text[core_s - 2].isdigit()):
            continue                      # the -25 in 2024-25
        token = core.strip()
        negative = token.startswith("(") and token.endswith(")")
        clean = token.strip("()").replace(",", "").strip()
        try:
            value = float(clean)
        except ValueError:
            continue
        out.append({"value": -value if negative else value,
                    "start": core_s, "end": core_e,
                    "before": text[max(0, core_s - 18):core_s]})
    return out


def _pick(text: str, cands: list):
    """Choose the number most likely to BE the reported value:
    1. drop label numbers (Scope 1 / Principle 6 / FY 2024) and bare years
    2. prefer the first number with a unit or scale word right after it
    3. else the first remaining candidate"""
    kept = []
    for c in cands:
        if _LABEL_BEFORE.search(c["before"]):
            continue
        is_whole = float(c["value"]).is_integer()
        if is_whole and _YEARISH.match(str(int(c["value"]))):
            after = text[c["end"]:c["end"] + 30]
            if not (_SCALE_RE.search(after) or _UNIT_RE.search(after)):
                continue                  # bare year = a period, not a value
        kept.append(c)
    if not kept:
        return None
    for c in kept:
        window = text[c["end"]:c["end"] + 30]
        if _SCALE_RE.search(window) or _UNIT_RE.search(window):
            return c
    return kept[0]


def parse_value(text: str) -> dict | None:
    """Extract + normalize (value, scale, unit, currency, sign) from text.

    The value is the number most likely to be the reported figure - not just
    the first digit on the page ("Scope 1" / "CO2e" / "FY 2024" are labels).
    Scale and unit are read from a window AROUND the picked number, so a
    different number's "crore" can never leak into this value.
    """
    text = text or ""
    cands = _candidates(text)
    if not cands:
        return None
    pick = _pick(text, cands)
    if pick is None:
        return None
    value = pick["value"]
    out = {"value": value, "value_base": value, "scale": "", "scale_factor": 1.0,
           "unit": "", "currency": "", "sign": -1 if value < 0 else 1}

    # scale + unit are local to the picked number (INR 1,234.5 crore style)
    window = text[max(0, pick["start"] - 15):pick["end"] + 35]
    m = _SCALE_RE.search(window)
    if m:
        out["scale"] = m.group(1).lower()
        out["scale_factor"] = SCALES.get(out["scale"], 1.0)
    m = _UNIT_RE.search(window)
    if m:
        out["unit"] = UNIT_ALIASES.get(m.group(1).lower().rstrip("."),
                                       m.group(1).upper())
        if out["unit"] in ("INR", "USD", "EUR", "IDR"):
            out["currency"] = out["unit"]
    if out["currency"] and not out["scale"]:
        # "INR 123" -> unit is currency, keep value as-is
        out["value_base"] = value
    else:
        out["value_base"] = value * out["scale_factor"]
    return out
