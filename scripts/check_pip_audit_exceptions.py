#!/usr/bin/env python3
"""Fail CI when a pip-audit exception review_by date has passed."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "security" / "pip-audit-exceptions.json"
data = json.loads(path.read_text(encoding="utf-8"))
today = date.today()
expired = []
for item in data.get("exceptions", []):
    review_by = date.fromisoformat(item["review_by"])
    if review_by < today:
        expired.append(f"{item['id']} review_by={item['review_by']}")
if expired:
    print("Expired pip-audit exceptions:\n" + "\n".join(expired), file=sys.stderr)
    raise SystemExit(1)
print(f"pip-audit exceptions current ({len(data.get('exceptions', []))} reviewed through dates)")
