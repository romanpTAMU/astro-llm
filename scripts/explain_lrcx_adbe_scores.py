#!/usr/bin/env python
"""Explain why LRCX and ADBE have high scores."""
import json
from pathlib import Path

def explain_scores():
    scored_file = Path("data/runs/2025-12-16_20-02-23/scored_candidates.json")
    stock_data_file = Path("data/runs/2025-12-16_20-02-23/stock_data.json")
    
    scored_data = json.load(open(scored_file, encoding='utf-8'))
    stock_data = json.load(open(stock_data_file, encoding='utf-8'))
    
    stock_map = {s["ticker"]: s for s in stock_data["data"]}
    
    print("=" * 80)
    print("WHY LRCX AND ADBE HAVE HIGH SCORES")
    print("=" * 80)
    print()
    
    for ticker in ["LRCX", "ADBE"]:
        scored = next((s for s in scored_data["candidates"] if s["ticker"] == ticker), None)
        stock = stock_map.get(ticker)
        
        if not scored or not stock:
            continue
        
        fund = stock["fundamentals"]
        fs = scored["factor_scores"]
        sent = scored["sentiment"]
        
        print(f"{ticker} BREAKDOWN:")
        print("-" * 80)
        print()
        
        print(f"1. QUALITY SCORE = {fs['quality']:.2f} (Weight: 25%)")
        print(f"   ROIC: {fund.get('roic'):.2f}% -> Score: 1.0 (excellent, >20%)")
        print(f"   Operating Margin: {fund.get('operating_margin_ttm'):.2f}% -> Score: 1.0 (excellent, >20%)")
        print(f"   Average: 1.0 (perfect quality score)")
        print(f"   Contribution to composite: {fs['quality'] * 0.25:.3f}")
        print()
        
        print(f"2. GROWTH SCORE = {fs['growth']:.2f} (Weight: 20%)")
        growth = fund.get('revenue_yoy_growth')
        if growth > 20:
            growth_desc = "excellent (>20%)"
        elif growth > 15:
            growth_desc = "very good (15-20%)"
        elif growth > 10:
            growth_desc = "good (10-15%)"
        else:
            growth_desc = "moderate"
        print(f"   Revenue Growth: {growth:.2f}% -> Score: {fs['growth']:.2f} ({growth_desc})")
        print(f"   Contribution to composite: {fs['growth'] * 0.20:.3f}")
        print()
        
        print(f"3. VALUE SCORE = {fs['value']:.2f} (Weight: 20%)")
        pe = fund.get('pe_ratio')
        ev = fund.get('ev_ebitda')
        fcf = fund.get('fcf_margin_ttm')
        
        # Calculate individual value components
        pe_score = None
        if pe <= 12:
            pe_score = 1.0
        elif pe <= 18:
            pe_score = 0.7
        elif pe <= 24:
            pe_score = 0.4
        elif pe <= 30:
            pe_score = 0.0
        
        ev_score = None
        if ev <= 8:
            ev_score = 1.0
        elif ev <= 12:
            ev_score = 0.7
        elif ev <= 16:
            ev_score = 0.4
        elif ev <= 20:
            ev_score = 0.0
        
        fcf_score = None
        if fcf >= 20:
            fcf_score = 1.0
        elif fcf >= 15:
            fcf_score = 0.7
        elif fcf >= 10:
            fcf_score = 0.4
        
        print(f"   P/E: {pe:.2f} -> Score: {pe_score:.1f}")
        print(f"   EV/EBITDA: {ev:.2f} -> Score: {ev_score:.1f}")
        print(f"   FCF Margin: {fcf:.2f}% -> Score: {fcf_score:.1f}")
        print(f"   Average: {fs['value']:.2f}")
        print(f"   Contribution to composite: {fs['value'] * 0.20:.3f}")
        print()
        
        print(f"4. SENTIMENT SCORE = {sent['sentiment_score']:.2f} (Weight: 15%)")
        print(f"   Analyst Consensus: {sent['analyst_consensus']}")
        print(f"   Price Target Upside: {sent['price_target_upside']:.2f}%")
        print(f"   Contribution to composite: {sent['sentiment_score'] * 0.15:.3f}")
        print()
        
        print(f"5. OTHER FACTORS:")
        print(f"   Stability: {fs['stability']:.2f} (Weight: 10%) -> {fs['stability'] * 0.10:.3f}")
        print(f"   Revisions: {fs['revisions']:.2f} (Weight: 10%) -> {fs['revisions'] * 0.10:.3f}")
        print(f"   Momentum: {fs['momentum']:.2f} (Weight: 5%) -> {fs['momentum'] * 0.05:.3f}")
        print()
        
        print(f"COMPOSITE SCORE: {scored['composite_score']:.3f}")
        print()
        print()

if __name__ == "__main__":
    explain_scores()

