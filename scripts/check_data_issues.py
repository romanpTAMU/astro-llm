#!/usr/bin/env python
"""
Scan latest run's stock_data.json for potential data issues:
- missing or zero fcf_margin_ttm
- zero volume
- missing market_cap
"""
import json
from pathlib import Path


def main():
    runs = sorted(Path("data/runs").glob("2025-12-16_*"))
    if not runs:
        print("No runs found matching 2025-12-16_*")
        return
    latest = runs[-1]
    stock_file = latest / "stock_data.json"
    if not stock_file.exists():
        print(f"No stock_data.json in {latest}")
        return

    data = json.loads(stock_file.read_text(encoding="utf-8"))
    missing_fcf = []
    zero_fcf = []
    zero_vol = []
    missing_mc = []

    for item in data.get("data", []):
        t = item.get("ticker")
        pd = item.get("price_data") or {}
        fund = item.get("fundamentals") or {}

        fcf_margin = fund.get("fcf_margin_ttm")
        if fcf_margin is None:
            missing_fcf.append(t)
        elif fcf_margin == 0:
            zero_fcf.append(t)

        if pd.get("volume") == 0:
            zero_vol.append(t)
        if pd.get("market_cap") is None:
            missing_mc.append(t)

    print(f"Latest run: {latest.name}")
    print(f"Tickers with fcf_margin_ttm missing: {len(missing_fcf)}")
    print(f"Tickers with fcf_margin_ttm == 0: {len(zero_fcf)}")
    print(f"Tickers with volume == 0: {len(zero_vol)}")
    print(f"Tickers with market_cap missing: {len(missing_mc)}")
    print("Sample zero_vol (first 15):", zero_vol[:15])


if __name__ == "__main__":
    main()


