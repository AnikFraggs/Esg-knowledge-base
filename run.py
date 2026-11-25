#!/usr/bin/env python3
"""
ESG Knowledge Base Pipeline - Entry Point
- Run without args for interactive terminal menu: python run.py
- Run with --ui or --web to launch the Drag & Drop Web UI directly: python run.py --ui
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--ui", "--web", "-u", "-w", "web", "ui"):
        import web_app
        import os
        port = int(os.getenv("PORT", 5000))
        print(f"\n=======================================================")
        print(f" ESG Knowledge Base UI running at: http://127.0.0.1:{port}")
        print(f" Drag & Drop files into the UI to feed them to uploads/")
        print(f"=======================================================\n")
        web_app.app.run(host="0.0.0.0", port=port, debug=False)
    else:
        from esg.ui.menu import main_menu
        main_menu()
