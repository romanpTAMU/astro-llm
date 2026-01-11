#!/usr/bin/env python
"""Verify LRCX and ADBE composite score calculations."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def verify_scores():
    scored_file = Path("data/runs/2025-12-16_20-02-23/scored_candidates.json")
    stock_data_file = Path("data/runs/2025-12-16_20-02-23/stock_data.json")
    
    scored_data = json.load(open(scored_file, encoding='utf-8'))
    stock_data = json.load(open(stock_data_file, encoding='utf-8'))
    
    stock_map = {s["ticker"]: s for s in stock_data["data"]}
    
    print("=" * 80)
    print("VERIFYING LRCX AND ADBE SCORES")
    print("=" * 80)
    print()
    
    for ticker in ["LRCX", "ADBE"]:
        scored = next((s for s in scored_data["candidates"] if s["ticker"] == ticker), None)
        stock = stock_map.get(ticker)
        
        if not scored or not stock:
            print(f"[ERROR] {ticker} not found")
            continue
        
        print(f"{ticker} ANALYSIS:")
        print("-" * 80)
        
        fund = stock["fundamentals"]
        fs = scored["factor_scores"]
        sent = scored["sentiment"]
        
        print(f"FUNDAMENTALS:")
        print(f"  Revenue Growth: {fund.get('revenue_yoy_growth'):.2f}%")
        print(f"  ROIC: {fund.get('roic'):.2f}%")
        print(f"  Operating Margin: {fund.get('operating_margin_ttm'):.2f}%")
        print(f"  P/E Ratio: {fund.get('pe_ratio'):.2f}")
        print(f"  EV/EBITDA: {fund.get('ev_ebitda'):.2f}")
        print(f"  FCF Margin: {fund.get('fcf_margin_ttm'):.2f}%")
        print()
        
        print(f"FACTOR SCORES:")
        print(f"  Value: {fs['value']:.3f}")
        print(f"  Quality: {fs['quality']:.3f}")
        print(f"  Growth: {fs['growth']:.3f}")
        print(f"  Stability: {fs['stability']:.3f}")
        print(f"  Revisions: {fs['revisions']:.3f}")
        print(f"  Momentum: {fs['momentum']:.3f}")
        print()
        
        print(f"SENTIMENT:")
        print(f"  Score: {sent['sentiment_score']:.3f}")
        print(f"  Price Target Upside: {sent['price_target_upside']:.2f}%")
        print()
        
        # Manual calculation
        weights = {
            "value": 0.20,
            "quality": 0.25,
            "growth": 0.20,
            "stability": 0.10,
            "revisions": 0.10,
            "momentum": 0.05,
            "sentiment": 0.15,
        }
        
        score = 0.0
        weight_sum = 0.0
        
        if fs['value'] is not None:
            score += fs['value'] * weights["value"]
            weight_sum += weights["value"]
        
        if fs['quality'] is not None:
            score += fs['quality'] * weights["quality"]
            weight_sum += weights["quality"]
        
        if fs['growth'] is not None:
            score += fs['growth'] * weights["growth"]
            weight_sum += weights["growth"]
        
        if fs['stability'] is not None:
            score += fs['stability'] * weights["stability"]
            weight_sum += weights["stability"]
        
        if fs['revisions'] is not None:
            score += fs['revisions'] * weights["revisions"]
            weight_sum += weights["revisions"]
        
        if fs['momentum'] is not None:
            score += fs['momentum'] * weights["momentum"]
            weight_sum += weights["momentum"]
        
        score += sent['sentiment_score'] * weights["sentiment"]
        weight_sum += weights["sentiment"]
        
        # Price target penalty
        if sent['price_target_upside'] is not None and sent['price_target_upside'] < 0:
            pt_penalty = max(-0.20, (sent['price_target_upside'] / 100.0) * 2)
            score += pt_penalty
            print(f"  Price Target Penalty: {pt_penalty:.3f}")
        
        if weight_sum > 0:
            calculated = score / weight_sum
        else:
            calculated = 0.0
        
        actual = scored["composite_score"]
        
        print(f"COMPOSITE SCORE:")
        print(f"  Calculated: {calculated:.6f}")
        print(f"  Actual: {actual:.6f}")
        print(f"  Difference: {abs(calculated - actual):.6f}")
        
        if abs(calculated - actual) < 0.001:
            print(f"  [OK] Scores match!")
        else:
            print(f"  [WARN] Scores don't match!")
        
        print()
        print()

if __name__ == "__main__":
    verify_scores()


