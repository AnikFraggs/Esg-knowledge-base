"""Layer 1: format router. Only PDF and XBRL/XLSX are accepted - by design."""


def detect(file_path: str) -> str:
    """Return one of: 'pdf' | 'xlsx' | 'xbrl_xml' | '' (unsupported)."""
    path = str(file_path).lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".xlsx", ".xlsm")):
        return "xlsx"
    if path.endswith((".xbrl", ".xml")):
        return "xbrl_xml"
    return ""
