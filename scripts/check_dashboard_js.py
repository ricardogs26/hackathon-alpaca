#!/usr/bin/env python3
"""Parse every <script> block of the dashboard with node (syntax only). Exit 1
on a syntax error — the check that would have caught 0.5.1. Used by `make lint`
and CI; skips with a notice if node is not installed."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "optionwright" / "api" / "static" / "dashboard.html"


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("check_dashboard_js: node not found, skipping")
        return 0
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", HTML.read_text(encoding="utf-8"), flags=re.S)
    if not blocks:
        print("check_dashboard_js: no <script> blocks found")
        return 1
    for i, js in enumerate(blocks, 1):
        proc = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(0,'utf8'))"],
                              input=js, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"check_dashboard_js: syntax error in script block {i}:\n{proc.stderr.strip()[:600]}")
            return 1
    print(f"check_dashboard_js: {len(blocks)} script block(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
