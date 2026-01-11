#!/usr/bin/env python
"""
Compare revenue YoY growth from:
1) Income statements (current vs prior)
2) FMP financial-growth endpoint (revenueGrowth)
"""

import sys
import time
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from agent.config import load_config  # noqa: E402


def fetch_income_growth(ticker: str, api_key: str) -> Optional[float]:
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
    params = {"apikey": api_key, "limit": 2}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data or len(data) < 2:
        return None
    current = data[0].get("revenue")
    prev = data[1].get("revenue")
    if not current or not prev:
        return None
    return ((current - prev) / prev) * 100


def fetch_growth_api(ticker: str, api_key: str) -> Optional[float]:
    url = "https://financialmodelingprep.com/stable/financial-growth"
    params = {"symbol": ticker, "apikey": api_key, "limit": 1}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and data:
        g = data[0].get("revenueGrowth")
        if g is not None:
            return g * 100  # convert decimal to percent
    elif isinstance(data, dict):
        g = data.get("revenueGrowth")
        if g is not None:
            return g * 100
    return None


def main():
    cfg = load_config()
    api_key = cfg.fmp_api_key
    if not api_key:
        print("No FMP_API_KEY configured")
        sys.exit(1)

    tickers = ["ADBE", "GOOGL", "MSFT", "AAPL", "AMZN", "NVDA"]
    print("ticker | income_stmt_growth% | financial_growth_api%")
    for t in tickers:
        try:
            inc = fetch_income_growth(t, api_key)
        except Exception as e:
            inc = f"error: {e}"
        time.sleep(0.25)
        try:
            fg = fetch_growth_api(t, api_key)
        except Exception as e:
            fg = f"error: {e}"
        time.sleep(0.25)
        print(f"{t:6} | {inc} | {fg}")


if __name__ == "__main__":
    main()


