"""
Utility to normalize legacy ticker 'FB' to 'META' across portfolio JSONs.

This is a safe, idempotent in-place fixer for historical run folders so
performance tracking stops being skewed by stale FB records.
"""

from __future__ import annotations

import json
from pathlib import Path


def fix_portfolio_file(path: Path) -> bool:
    """Replace ticker FB with META in a portfolio JSON file.

    Returns True if a change was written, False otherwise.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    holdings = data.get("holdings")
    if not isinstance(holdings, list):
        return False

    changed = False
    for h in holdings:
        if isinstance(h, dict) and h.get("ticker") == "FB":
            h["ticker"] = "META"
            changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return changed


def main() -> None:
    root = Path("data/runs")
    targets = list(root.glob("**/portfolio*.json"))

    updated = 0
    for path in targets:
        if path.is_file() and fix_portfolio_file(path):
            updated += 1

    print(f"Processed {len(targets)} portfolio files; updated {updated}")


if __name__ == "__main__":
    main()

