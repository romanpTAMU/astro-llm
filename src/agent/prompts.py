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


def user_themes(horizon_end: date, remaining_days: int) -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    return dedent(
        f"""
        Current date: {today_str}
        Investment horizon ends: {horizon_end.strftime("%Y-%m-%d")} (approximately {remaining_days} days remaining)
        
        Task:
        Identify 5-8 major market themes that are likely to drive US equity performance through 
        {horizon_end.strftime("%Y-%m-%d")}. Consider:
        - Technological disruption (AI, automation, cloud, etc.)
        - Demographic shifts
        - Regulatory/policy changes
        - Energy transition and sustainability
        - Health/biotech innovation
        - Infrastructure spending
        - Consumer behavior changes
        - Geopolitical developments affecting US markets
        
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
        to benefit. Include ticker, sector, rationale explaining theme connection, and the theme name.
        
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
