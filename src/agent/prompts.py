from __future__ import annotations

from textwrap import dedent
from datetime import date


def system_universe() -> str:
    return dedent(
        """
        You are a buy-side equity PM agent. You must propose a US equities candidate universe consistent with explicit constraints. 
        Rules:
        - Never invent facts or numbers; only reason from constraints.
        - US-listed common stocks only. Exclude ETFs, ETNs, ADRs, SPACs, closed-end funds.
        - Output must be strict JSON.
        """
    ).strip()


def user_universe(
    remaining_days: int,
    target_count: int,
    min_weight: float,
    max_weight: float,
    sector_cap: float,
    industry_cap: float,
    liquidity_dollar_min: int,
) -> str:
    return dedent(
        f"""
        Constraints:
        - Target {target_count} candidate tickers; only US-listed common stocks.
        - Portfolio horizon ends on 2026-05-15 with remainingDays={remaining_days}.
        - Weight bounds 2–10% per name (min={min_weight}, max={max_weight}).
        - Soft caps: sector ≤ {sector_cap*100:.0f}%, industry ≤ {industry_cap*100:.0f}%.
        - Liquidity: average daily dollar volume ≥ ${liquidity_dollar_min:,}.
        
        Task:
        - Propose top {target_count} candidates with a brief one-line rationale and sector if known.
        - Prefer liquid, seasoned names; avoid OTC and penny stocks.
        
        Output JSON strictly as:
        {{
          "candidates": [
             {{"ticker": "AAPL", "sector": "Information Technology", "rationale": "iPhone cycle strength; services mix growth."}},
             ... exactly {target_count} items if feasible ...
          ]
        }}
        """
    ).strip()


def system_themes() -> str:
    return dedent(
        """
        You are a macro and thematic equity research analyst. Identify major market themes that are 
        likely to drive stock performance over the investment horizon. Focus on structural, secular 
        trends rather than short-term noise.
        Rules:
        - Identify 5-8 major themes that span multiple sectors when possible.
        - Consider macroeconomic, technological, demographic, and regulatory drivers.
        - Themes should be actionable for US equity selection.
        - Output must be strict JSON.
        """
    ).strip()


def user_themes(horizon_end: date, remaining_days: int, general_news: list = None) -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    
    news_context = ""
    if general_news:
        # Summarize top news articles
        top_news = general_news[:20]  # Use top 20 most recent
        news_context = "\n\nRecent Market News (last 7 days):\n"
        for item in top_news:
            news_context += f"- {item.get('title', '')} ({item.get('publisher', 'Unknown')})\n"
            if item.get('text'):
                # Truncate long text
                text = item['text']
                if len(text) > 200:
                    text = text[:200] + "..."
                news_context += f"  {text}\n"
    
    return dedent(
        f"""
        Current date: {today_str}
        Investment horizon ends: {horizon_end.strftime("%Y-%m-%d")} (approximately {remaining_days} days remaining)
        {news_context}
        
        Task:
        Based on the recent market news above and your knowledge, identify 5-8 major market themes that are likely to drive US equity performance through 
        {horizon_end.strftime("%Y-%m-%d")}. Consider:
        - Technological disruption (AI, automation, cloud, etc.)
        - Demographic shifts
        - Regulatory/policy changes
        - Energy transition and sustainability
        - Health/biotech innovation
        - Infrastructure spending
        - Consumer behavior changes
        - Geopolitical developments affecting US markets
        - Trends evident in recent news
        - Memory/compute gap
        
        Output JSON strictly as:
        {{
          "themes": [
            {{
              "name": "AI Infrastructure Buildout",
              "description": "Enterprise and cloud providers accelerating AI infrastructure investments; 
              semiconductor, data center, and AI software companies benefiting from sustained capital 
              allocation.",
              "timeframe": "next 12-24 months"
            }},
            ... 5-8 total themes ...
          ]
        }}
        """
    ).strip()


def system_theme_candidates() -> str:
    return dedent(
        """
        You are a buy-side equity PM agent. Generate stock candidates aligned with specific market themes.
        Rules:
        - Never invent facts or numbers; only reason from themes and constraints.
        - US-listed common stocks only. Exclude ETFs, ETNs, ADRs, SPACs, closed-end funds.
        - For each theme, propose 3-5 best-positioned US equities.
        - Output must be strict JSON.
        """
    ).strip()


def user_theme_candidates(
    themes: list[dict],
    remaining_days: int,
    min_weight: float,
    max_weight: float,
    liquidity_dollar_min: int,
) -> str:
    themes_text = "\n".join([
        f"- {t['name']}: {t['description']} ({t['timeframe']})"
        for t in themes
    ])
    
    return dedent(
        f"""
        Market Themes:
        {themes_text}
        
        Constraints:
        - Portfolio horizon ends on 2026-05-15 with remainingDays={remaining_days}.
        - Weight bounds 2–10% per name (min={min_weight}, max={max_weight}).
        - Liquidity: average daily dollar volume ≥ ${liquidity_dollar_min:,}.
        
        Task:
        For each theme above, propose 3-5 US-listed common stock candidates that are best positioned 
        to benefit. Include ticker, sector, rationale explaining theme connection, and the theme name 
        (ONLY the theme name from the "name" field, not the description or timeframe).
        
        Output JSON strictly as:
        {{
          "candidates": [
            {{
              "ticker": "NVDA",
              "sector": "Information Technology",
              "rationale": "Dominant GPU provider for AI training/inference; data center revenue growth.",
              "theme": "AI Infrastructure Buildout"
            }},
            ... 15-40 total candidates (3-5 per theme) ...
          ]
        }}
        """
    ).strip()


def system_portfolio() -> str:
    return dedent(
        """
        You are a buy-side equity PM constructing a long-only portfolio from scored candidates.
        Rules:
        - Select exactly 20 stocks from the scored candidates provided.
        - Assign weights between 2% and 10% per stock (0.02 to 0.10).
        - Weights must sum to exactly 100% (1.0).
        - Diversify across sectors and industries (respect sector_cap and industry_cap constraints).
        - Prioritize stocks with higher composite scores, but ensure diversification.
        - Consider sector/industry balance and avoid over-concentration.
        - Output must be strict JSON.
        """
    ).strip()


def user_portfolio(
    scored_candidates: list[dict],
    remaining_days: int,
    min_weight: float,
    max_weight: float,
    sector_cap: float,
    industry_cap: float,
    horizon_end: date,
) -> str:
    # Sort candidates by composite score (highest first) and show top 40 for selection
    sorted_candidates = sorted(scored_candidates, key=lambda x: x.get("composite_score", -999), reverse=True)
    top_candidates = sorted_candidates[:min(40, len(sorted_candidates))]
    
    candidates_text = []
    for cand in top_candidates:
        score = cand.get("composite_score", 0)
        sector = cand.get("sector", "Unknown")
        theme = cand.get("theme")
        sentiment = cand.get("sentiment", {}).get("overall_sentiment", "unknown")
        price = cand.get("price", "N/A")
        news_summary = cand.get("news_summary")
        
        line = f"  {cand['ticker']}: score={score:.3f}, sector={sector}, sentiment={sentiment}, price=${price}"
        if theme:
            line += f", theme={theme}"
        candidates_text.append(line)
        
        # Add news summary if available
        if news_summary:
            candidates_text.append(f"    News: {news_summary}")
    
    return dedent(
        f"""
        Portfolio Construction Task:
        
        Investment Horizon: Today to {horizon_end.strftime("%Y-%m-%d")} ({remaining_days} days remaining)
        
        Constraints:
        - Select EXACTLY 20 stocks
        - Weight per stock: {min_weight*100:.0f}% to {max_weight*100:.0f}% (0.02 to 0.10)
        - Total weight must sum to EXACTLY 100% (1.0)
        - Sector cap: {sector_cap*100:.0f}% (no single sector > {sector_cap*100:.0f}%)
        - Industry cap: {industry_cap*100:.0f}% (no single industry > {industry_cap*100:.0f}%)
        - Long-only portfolio (no short positions)
        
        Scored Candidates (sorted by composite score, showing top 40):
        {chr(10).join(candidates_text)}
        
        Instructions:
        1. Select the best 20 stocks considering:
           - Composite score (higher is better)
           - Sector diversification (aim for 3-6 sectors, none > {sector_cap*100:.0f}%)
           - Industry diversification (none > {industry_cap*100:.0f}%)
           - Sentiment alignment (prefer bullish/neutral over bearish)
           - Theme exposure (balance themed and non-themed stocks)
        
        2. Assign weights:
           - Higher scores can get higher weights (up to {max_weight*100:.0f}%)
           - Lower scores should get lower weights (minimum {min_weight*100:.0f}%)
           - Ensure weights sum to exactly 1.0 (100%)
        
        3. Validate:
           - Exactly 20 holdings
           - Each weight between {min_weight} and {max_weight}
           - Total weight = 1.0
           - No sector exceeds {sector_cap*100:.0f}%
           - No industry exceeds {industry_cap*100:.0f}%
        
        Output JSON strictly as:
        {{
          "holdings": [
            {{
              "ticker": "AAPL",
              "weight": 0.08,
              "sector": "Information Technology",
              "theme": null,
              "rationale": "High composite score (0.75), strong sentiment, sector leader",
              "composite_score": 0.75
            }},
            ... exactly 20 holdings ...
          ]
        }}
        """
    ).strip()
