#!/usr/bin/env python3
"""ESG Knowledge Base Unified Server Entrypoint."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from esg import settings
from web_app import app

if __name__ == "__main__":
    settings.ensure_dirs()
    port = int(os.getenv("PORT", 5000))
    print(f"\n=======================================================")
    print(f" ESG Knowledge Base Server running on http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
