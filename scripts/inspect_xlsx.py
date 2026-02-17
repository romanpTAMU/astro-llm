#!/usr/bin/env python3
"""Inspect an xlsx file: sheet names, dimensions, headers, and sample rows."""
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    print("Install openpyxl: pip install openpyxl")
    sys.exit(1)

def main():
    path = Path(r"C:\Users\Roman\Downloads\donny_q2_2025 2.xlsx")
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    out_path = Path(__file__).parent.parent / "donny_q2_2025_structure.txt"
    lines = []

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines.append("=" * 80)
    lines.append(f"FILE: {path.name}")
    lines.append("=" * 80)
    lines.append(f"Sheets: {[s.title for s in wb.worksheets]}")
    lines.append("")

    for ws in wb.worksheets:
        lines.append("=" * 80)
        lines.append(f"SHEET: {ws.title}")
        lines.append("=" * 80)
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            lines.append("(empty)")
            lines.append("")
            continue
        # Max columns from first 20 rows
        max_cols = max(len(r) for r in rows[:20]) if rows else 0
        # Header (row 1)
        header = list(rows[0]) if rows else []
        header = header + [None] * (max_cols - len(header))
        lines.append("COLUMNS (row 1):")
        for c, val in enumerate(header, 1):
            if val is not None and str(val).strip():
                lines.append(f"  Col {c}: {val}")
        lines.append("")
        lines.append("SAMPLE ROWS (first 15):")
        for i, row in enumerate(rows[:15], 1):
            r = list(row)[:max_cols] if row else []
            r = ["" if v is None else str(v).strip()[:50] for v in r]
            lines.append(f"  Row {i}: {r}")
        lines.append("")
        if len(rows) > 15:
            lines.append(f"... ({len(rows)} rows total)")
            lines.append("")
    wb.close()

    text = "\n".join(lines)
    out_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWritten to: {out_path}")

if __name__ == "__main__":
    main()
