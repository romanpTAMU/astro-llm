from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
from openai import OpenAI

from .config import load_config
from .openai_client import get_client, chat_json
from .models import (
    StockDataResponse,
    ScoredStock,
    ScoredCandidatesResponse,
    FactorScores,
    SentimentAnalysis,
    RiskFlags,
)

app = typer.Typer(add_completion=False)

TICKER_NORMALIZATION_MAP = {
    "FB": "META",
}


def normalize_ticker(ticker: str) -> str:
    return TICKER_NORMALIZATION_MAP.get(ticker, ticker)


def calculate_value_score(fundamentals) -> Optional[float]:
    """Calculate value factor score from EV/EBITDA, FCF margin, P/E."""
    if not fundamentals:
        return None
    
    scores = []
    
    # EV/EBITDA (lower is better). Use smoother buckets to avoid cliff effects.
    ev = fundamentals.ev_ebitda
    if ev is not None and ev > 0:
        if ev <= 8:
            scores.append(1.0)
        elif ev <= 12:
            scores.append(0.7)
        elif ev <= 16:
            scores.append(0.4)
        elif ev <= 20:
            scores.append(0.0)
        elif ev <= 25:
            scores.append(-0.3)
        else:
            scores.append(-0.6)
    
    # P/E ratio (lower is better)
    pe = fundamentals.pe_ratio
    if pe is not None and pe > 0:
        if pe <= 12:
            scores.append(1.0)
        elif pe <= 18:
            scores.append(0.7)
        elif pe <= 24:
            scores.append(0.4)
        elif pe <= 30:
            scores.append(0.0)
        elif pe <= 40:
            scores.append(-0.3)
        else:
            scores.append(-0.6)
    
    # FCF margin (higher is better proxy for FCF yield)
    fcf = fundamentals.fcf_margin_ttm
    if fcf is not None:
        if fcf >= 20:
            scores.append(1.0)
        elif fcf >= 15:
            scores.append(0.7)
        elif fcf >= 10:
            scores.append(0.4)
        elif fcf >= 5:
            scores.append(0.0)
        elif fcf >= 0:
            scores.append(-0.3)
        else:
            scores.append(-0.6)
    
    if not scores:
        return None
    
    return max(-1.0, min(1.0, statistics.mean(scores)))


def calculate_quality_score(fundamentals) -> Optional[float]:
    """Calculate quality factor score from ROIC, margins."""
    if not fundamentals:
        return None
    
    scores = []
    
    # ROIC (higher is better)
    if fundamentals.roic is not None:
        if fundamentals.roic > 20:
            scores.append(1.0)
        elif fundamentals.roic > 15:
            scores.append(0.7)
        elif fundamentals.roic > 10:
            scores.append(0.4)
        elif fundamentals.roic > 5:
            scores.append(0.0)
        else:
            scores.append(-0.5)
    
    # Operating margin (higher is better)
    if fundamentals.operating_margin_ttm is not None:
        if fundamentals.operating_margin_ttm > 20:
            scores.append(1.0)
        elif fundamentals.operating_margin_ttm > 15:
            scores.append(0.7)
        elif fundamentals.operating_margin_ttm > 10:
            scores.append(0.4)
        elif fundamentals.operating_margin_ttm > 5:
            scores.append(0.0)
        else:
            scores.append(-0.3)
    
    return statistics.mean(scores) if scores else None


def calculate_growth_score(fundamentals) -> Optional[float]:
    """Calculate growth factor score from revenue growth."""
    if not fundamentals or fundamentals.revenue_yoy_growth is None:
        return None
    
    growth = fundamentals.revenue_yoy_growth
    
    # Higher growth = better score
    if growth > 20:
        return 1.0
    elif growth > 15:
        return 0.7
    elif growth > 10:
        return 0.5
    elif growth > 5:
        return 0.2
    elif growth > 0:
        return 0.0
    else:
        return -0.5  # Negative growth is bad


def calculate_stability_score(price_data) -> Optional[float]:
    """Calculate stability factor score using beta if available; fall back to short-term volatility."""
    if not price_data:
        return None
    
    if price_data.beta is not None:
        beta = price_data.beta
        if beta < 0.8:
            return 0.7  # lower beta = more stable
        elif beta < 1.0:
            return 0.4
        elif beta < 1.2:
            return 0.1
        elif beta < 1.5:
            return -0.2
        else:
            return -0.5
    
    # Fallback: 1d absolute price change as a crude proxy
    if price_data.price_change_pct is None:
        return None
    abs_change = abs(price_data.price_change_pct)
    if abs_change < 2:
        return 0.5
    elif abs_change < 5:
        return 0.0
    else:
        return -0.3


def calculate_revisions_score(analyst_recs) -> Optional[float]:
    """Calculate revisions factor score from analyst changes.
    
    Uses weighted score based on buy/hold/sell counts to account for analyst bias.
    """
    if not analyst_recs:
        return None
    
    score = 0.0
    
    # Use raw counts if available for more nuanced scoring
    if analyst_recs.buy_count is not None and analyst_recs.hold_count is not None and analyst_recs.sell_count is not None:
        total = analyst_recs.buy_count + analyst_recs.hold_count + analyst_recs.sell_count
        if total > 0:
            # Weighted score: Buy = +1, Hold = 0, Sell = -1
            # Normalize to [-1, 1] range
            buy_pct = analyst_recs.buy_count / total
            sell_pct = analyst_recs.sell_count / total
            score = buy_pct - sell_pct  # Range: [-1, 1]
        else:
            score = 0.0
    else:
        # Fallback to consensus if counts not available
        if analyst_recs.consensus == "Buy":
            score += 0.5
        elif analyst_recs.consensus == "Hold":
            score += 0.0
        elif analyst_recs.consensus == "Sell":
            score -= 0.5
    
    # Recent upgrades/downgrades
    if analyst_recs.recent_changes:
        for change in analyst_recs.recent_changes:
            if "upgrade" in change.lower() or "positive" in change.lower():
                score += 0.2
            elif "downgrade" in change.lower() or "negative" in change.lower():
                score -= 0.2
    
    # Price target upside (if available)
    if analyst_recs.price_target and analyst_recs.price_target > 0:
        # This would need current price to calculate upside
        # For now, just having a price target is positive
        score += 0.1
    
    return max(-1.0, min(1.0, score))  # Clamp to [-1, 1]


def calculate_momentum_score(price_data) -> Optional[float]:
    """Light-touch momentum score using short/medium horizon returns.
    
    Preference order: ~20-day, then ~5-day, then 1-day.
    Scales to [-1, 1] with soft caps to avoid overpowering long-term factors.
    """
    if not price_data:
        return None
    
    horizons = [
        ("20d", getattr(price_data, "price_change_pct_20d", None), 20.0),
        ("5d", getattr(price_data, "price_change_pct_5d", None), 12.0),
        ("1d", getattr(price_data, "price_change_pct", None), 10.0),
    ]
    
    for _, pct, scale in horizons:
        if pct is not None:
            return max(-1.0, min(1.0, pct / scale))
    
    return None


def summarize_news(
    ticker: str,
    news_items: list,
    client: OpenAI,
    model: str,
) -> Optional[str]:
    """Summarize news articles into 3-4 sentences.
    
    Args:
        ticker: Stock ticker
        news_items: List of NewsItem objects
        client: OpenAI client
        model: Model name
    
    Returns:
        News summary string (3-4 sentences) or None if no news
    """
    if not news_items:
        return None
    
    # Prepare news data for LLM
    news_data = []
    for item in news_items[:10]:  # Use top 10 most recent articles
        news_data.append({
            "headline": item.headline,
            "summary": item.summary or "",
            "source": item.source,
            "published_at": item.published_at.strftime("%Y-%m-%d") if item.published_at else None,
        })
    
    system = """You are a financial news summarizer. Create a concise 3-4 sentence summary of recent news articles about a stock.
Focus on the most important developments, trends, and key information that would be relevant for investment decisions.
Be factual and objective."""
    
    user = f"""Summarize the following recent news articles about {ticker} into 3-4 sentences:

{json.dumps(news_data, indent=2)}

Provide a concise summary that captures:
- Key developments and events
- Important trends or patterns
- Significant business updates
- Market-relevant information

Output JSON with a "summary" field containing the summary text (3-4 sentences):
{{
  "summary": "Your 3-4 sentence summary here..."
}}"""
    
    try:
        result = chat_json(client, model, system, user, timeout=60.0)
        summary = result.get("summary")
        if summary:
            return str(summary).strip()
        
        # Fallback: try other common keys
        for key in ["text", "content", "news_summary", "news"]:
            if key in result:
                value = result[key]
                if isinstance(value, str):
                    return value.strip()
        
        return None
    except Exception as e:
        typer.echo(f"  [WARN] Error summarizing news for {ticker}: {e}")
        return None


def synthesize_sentiment(
    ticker: str,
    analyst_recs,
    news_items: list,
    price_data,
    client: OpenAI,
    model: str,
    as_of_date: Optional[date] = None,
    model_cutoff: Optional[date] = None,
) -> SentimentAnalysis:
    """Synthesize sentiment from analyst recs and news using LLM."""
    # Normalize legacy tickers (e.g., FB -> META) to avoid stale data artifacts
    if ticker == "FB":
        ticker = "META"
    
    def normalize_price_target(target: Optional[float], current_price: Optional[float]) -> Optional[float]:
        """Adjust obviously unadjusted targets (e.g., pre-split values)."""
        if not target or not current_price or current_price <= 0:
            return target
        
        ratio = target / current_price
        # Detect common split ratios (2x, 3x, 4x, 5x, 10x) with tolerance
        for split in (10, 5, 4, 3, 2):
            if 0.6 * split <= ratio <= 1.4 * split:
                return target / split
        return target
    
    typer.echo(f"    [DEBUG] Starting sentiment synthesis for {ticker}")
    typer.echo(f"    [DEBUG] Analyst recs available: {analyst_recs is not None}")
    typer.echo(f"    [DEBUG] News items count: {len(news_items)}")
    
    # Build context
    analyst_info = []
    if analyst_recs:
        analyst_info.append(f"Consensus: {analyst_recs.consensus}")
        if analyst_recs.buy_count is not None and analyst_recs.hold_count is not None and analyst_recs.sell_count is not None:
            total_recs = analyst_recs.buy_count + analyst_recs.hold_count + analyst_recs.sell_count
            if total_recs > 0:
                buy_pct = (analyst_recs.buy_count / total_recs) * 100
                hold_pct = (analyst_recs.hold_count / total_recs) * 100
                sell_pct = (analyst_recs.sell_count / total_recs) * 100
                analyst_info.append(f"Recommendations: Buy {analyst_recs.buy_count} ({buy_pct:.1f}%), Hold {analyst_recs.hold_count} ({hold_pct:.1f}%), Sell {analyst_recs.sell_count} ({sell_pct:.1f}%)")
        if analyst_recs.price_target:
            analyst_info.append(f"Price Target: ${analyst_recs.price_target:.2f}")
        if analyst_recs.recent_changes:
            analyst_info.append(f"Recent Changes: {', '.join(analyst_recs.recent_changes)}")
    
    # Filter news by date in backtest mode
    if as_of_date:
        news_items = [
            n for n in news_items 
            if n.published_at and n.published_at.date() <= as_of_date
        ]
    
    news_summary = []
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    
    for news in news_items[:5]:  # Top 5 news items (reduced for cost efficiency)
        sentiment = news.sentiment or "neutral"
        if sentiment == "bullish":
            bullish_count += 1
        elif sentiment == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1
        
        news_summary.append(f"- {news.headline} ({sentiment})")
    
    # Build date-aware system prompt
    date_instruction = ""
    if as_of_date:
        date_instruction = f"""
CRITICAL: You are analyzing this stock as of {as_of_date.strftime('%Y-%m-%d')}. 
You must ONLY use information that would have been available on or before this date.
Do not use any knowledge of events that occurred after {as_of_date.strftime('%Y-%m-%d')}.
"""
        if model_cutoff:
            date_instruction += f"Your training data cutoff is {model_cutoff.strftime('%Y-%m-%d')}. Act as if you have no knowledge beyond the analysis date."
    
    system = f"""You are a financial sentiment analyst. Synthesize sentiment from analyst recommendations and news.
{date_instruction}
Output JSON only."""
    
    date_context = f" (as of {as_of_date.strftime('%Y-%m-%d')})" if as_of_date else ""
    technicals_lines = []
    if price_data:
        if getattr(price_data, "sma_20", None) is not None and getattr(price_data, "sma_50", None) is not None:
            technicals_lines.append(f"SMA20: {price_data.sma_20:.2f}, SMA50: {price_data.sma_50:.2f}")
        elif getattr(price_data, "sma_20", None) is not None:
            technicals_lines.append(f"SMA20: {price_data.sma_20:.2f}")
        if getattr(price_data, "rsi_14", None) is not None:
            technicals_lines.append(f"RSI14: {price_data.rsi_14:.1f}")
    technicals_block = "\n".join(technicals_lines) if technicals_lines else "No technical indicators available"
    user = f"""Ticker: {ticker}{date_context}
    
Analyst Information:
{chr(10).join(analyst_info) if analyst_info else 'No analyst data available'}

Technical Indicators:
{technicals_block}

News Sentiment Summary:
- Bullish: {bullish_count}
- Neutral: {neutral_count}
- Bearish: {bearish_count}

Recent News Headlines:
{chr(10).join(news_summary) if news_summary else 'No recent news'}

Current Price: ${price_data.price if price_data else 'N/A'}

Analyze and synthesize the overall sentiment. Provide:
1. Overall sentiment (bullish/neutral/bearish)
2. Sentiment score (-1 to 1, where 1 is most bullish)
3. Key positive drivers (2-3 items)
4. Key risks/concerns (2-3 items)
5. Price target upside % if price target available

Output JSON:
{{
  "overall_sentiment": "bullish" or "neutral" or "bearish",
  "sentiment_score": number between -1 and 1,
  "analyst_consensus": "Buy" or "Hold" or "Sell" or null,
  "analyst_score": number between -1 and 1 or null,
  "news_sentiment": "bullish" or "neutral" or "bearish" or null,
  "news_score": number between -1 and 1 or null,
  "key_drivers": ["driver1", "driver2"],
  "key_risks": ["risk1", "risk2"],
  "price_target_upside": number or null
}}"""
    
    try:
        typer.echo(f"    [DEBUG] Calling LLM ({model}) for sentiment synthesis...")
        result = chat_json(client, model, system, user)
        typer.echo(f"    [DEBUG] LLM call completed, parsing result...")
        
        # Calculate price target upside if we have both
        price_target_upside = None
        if analyst_recs and analyst_recs.price_target and price_data:
            current_price = price_data.price
            adjusted_target = normalize_price_target(analyst_recs.price_target, current_price)
            if adjusted_target != analyst_recs.price_target:
                typer.echo(
                    f"    [WARN] Adjusted price target for {ticker} from "
                    f"{analyst_recs.price_target} to {adjusted_target} (possible split)"
                )
            if current_price > 0 and adjusted_target and adjusted_target > 0:
                price_target_upside = ((adjusted_target - current_price) / current_price) * 100
                # Cap extreme upside to avoid runaway scores from bad targets
                if price_target_upside > 400:
                    typer.echo(
                        f"    [WARN] Price target upside {price_target_upside:.1f}% for {ticker} exceeds cap; capping to 400%"
                    )
                    price_target_upside = 400.0
        
        return SentimentAnalysis(
            overall_sentiment=result.get("overall_sentiment", "neutral"),
            sentiment_score=float(result.get("sentiment_score", 0.0)),
            analyst_consensus=result.get("analyst_consensus") or (analyst_recs.consensus if analyst_recs else None),
            analyst_score=result.get("analyst_score"),
            news_sentiment=result.get("news_sentiment"),
            news_score=result.get("news_score"),
            key_drivers=result.get("key_drivers", []),
            key_risks=result.get("key_risks", []),
            price_target_upside=price_target_upside if price_target_upside is not None else result.get("price_target_upside"),
        )
    except Exception as e:
        typer.echo(f"  [WARN] Error synthesizing sentiment for {ticker}: {e}")
        # Return neutral sentiment as fallback
        return SentimentAnalysis(
            overall_sentiment="neutral",
            sentiment_score=0.0,
            key_drivers=[],
            key_risks=[],
        )


def apply_risk_screens(
    ticker: str,
    price_data,
    cfg,
) -> RiskFlags:
    """Apply hard risk screens."""
    failed_checks = []
    
    # Liquidity check disabled (FMP often returns zero volume placeholders)
    liquidity_ok = True
    
    # Price check
    price_ok = True
    if price_data and price_data.price:
        if price_data.price < 3.0:  # Minimum $3 price
            price_ok = False
            failed_checks.append(f"Price too low (${price_data.price:.2f} < $3.00)")
    else:
        price_ok = False
        failed_checks.append("No price data")
    
    # Trading halts (would need real-time data - placeholder)
    no_trading_halts = True
    # TODO: Check for trading halts if we have real-time data source
    
    # Pending M&A (would need real-time data - placeholder)
    no_pending_ma = True
    # TODO: Check for pending M&A announcements
    
    # Earnings (would need calendar data - placeholder)
    earnings_clear = True
    # TODO: Check if earnings within 2 trading days
    
    passed_all_checks = len(failed_checks) == 0
    
    return RiskFlags(
        passed_all_checks=passed_all_checks,
        failed_checks=failed_checks,
        liquidity_ok=liquidity_ok,
        price_ok=price_ok,
        no_trading_halts=no_trading_halts,
        no_pending_ma=no_pending_ma,
        earnings_clear=earnings_clear,
    )


def calculate_composite_score(
    factor_scores: FactorScores,
    sentiment: SentimentAnalysis,
    risk_flags: RiskFlags,
) -> float:
    """Calculate composite score combining factors and sentiment."""
    # If failed risk checks, heavily penalize
    if not risk_flags.passed_all_checks:
        return -10.0  # Effectively disqualify
    
    # Weight factors
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
    
    # Add factor scores
    if factor_scores.value is not None:
        score += factor_scores.value * weights["value"]
        weight_sum += weights["value"]
    
    if factor_scores.quality is not None:
        score += factor_scores.quality * weights["quality"]
        weight_sum += weights["quality"]
    
    if factor_scores.growth is not None:
        score += factor_scores.growth * weights["growth"]
        weight_sum += weights["growth"]
    
    if factor_scores.stability is not None:
        score += factor_scores.stability * weights["stability"]
        weight_sum += weights["stability"]
    
    if factor_scores.revisions is not None:
        score += factor_scores.revisions * weights["revisions"]
        weight_sum += weights["revisions"]
    
    if getattr(factor_scores, "momentum", None) is not None:
        score += factor_scores.momentum * weights["momentum"]
        weight_sum += weights["momentum"]
    
    # Add sentiment score
    score += sentiment.sentiment_score * weights["sentiment"]
    weight_sum += weights["sentiment"]

    # Penalty for negative price target upside to avoid over-allocating to names
    # that are trading above consensus targets. Doubled from previous version.
    if sentiment.price_target_upside is not None and sentiment.price_target_upside < 0:
        # Scale penalty: -5% upside -> -0.10 (doubled), capped at -0.20
        pt_penalty = max(-0.20, (sentiment.price_target_upside / 100.0) * 2)
        score += pt_penalty
    
    # Normalize by actual weight sum (in case some factors are missing)
    if weight_sum > 0:
        score = score / weight_sum
    
    return score


@app.command()
def score(
    stock_data_file: Path = typer.Option(
        Path("data/stock_data.json"), help="Input stock data JSON path"
    ),
    candidates_file: Path = typer.Option(
        None, help="Original candidates file for sector/theme info (defaults to merged_candidates.json, falls back to candidates.json)"
    ),
    out: Path = typer.Option(
        Path("data/scored_candidates.json"), help="Output JSON path"
    ),
    model: Optional[str] = typer.Option(None, help="OpenAI model override (defaults to cheap_model for efficiency)"),
):
    """Score candidates using factor analysis, sentiment synthesis, and risk screens."""
    cfg = load_config()
    # Use cheap model by default for sentiment synthesis (high volume, doesn't need complex reasoning)
    chosen_model = model or cfg.cheap_model
    
    if not stock_data_file.exists():
        typer.echo(f"Stock data file not found: {stock_data_file}")
        typer.echo("Run Phase 2 first: python main.py data fetch")
        raise typer.Exit(code=1)
    
    # Load stock data
    stock_data_text = stock_data_file.read_text(encoding='utf-8')
    if not stock_data_text or not stock_data_text.strip():
        typer.echo(f"Stock data file is empty: {stock_data_file}")
        typer.echo("Run Phase 2 first: python main.py data fetch")
        raise typer.Exit(code=1)
    
    try:
        stock_data_json = json.loads(stock_data_text)
    except json.JSONDecodeError as e:
        typer.echo(f"Invalid JSON in stock data file: {e}")
        typer.echo(f"File: {stock_data_file}")
        raise typer.Exit(code=1)
    
    try:
        stock_data_resp = StockDataResponse.model_validate(stock_data_json)
    except Exception as e:
        typer.echo(f"Failed to parse stock data: {e}")
        raise typer.Exit(code=1)
    
    # Auto-detect candidates file if not provided
    if candidates_file is None:
        merged_file = Path("data/merged_candidates.json")
        regular_file = Path("data/candidates.json")
        if merged_file.exists():
            candidates_file = merged_file
        elif regular_file.exists():
            candidates_file = regular_file
            typer.echo(f"  [INFO] Using {regular_file.name} (merged_candidates.json not found)")
        else:
            typer.echo(f"  [WARN] Candidates file not found. Continuing without sector/theme info...")
            candidates_file = None
    
    # Load candidates for sector/theme info
    candidates_map = {}
    if candidates_file and candidates_file.exists():
        candidates_text = candidates_file.read_text(encoding='utf-8')
        if candidates_text and candidates_text.strip():
            try:
                candidates_json = json.loads(candidates_text)
                from .models import CandidateResponse
                candidates_resp = CandidateResponse.model_validate(candidates_json)
                candidates_map = {}
                for c in candidates_resp.candidates:
                    candidates_map[c.ticker] = c
                    candidates_map[normalize_ticker(c.ticker)] = c
            except Exception as e:
                typer.echo(f"  [WARN] Could not load candidates file: {e}")
                typer.echo("  Continuing without sector/theme info...")
    
    client = get_client()
    scored_stocks = []
    
    typer.echo(f"Scoring {len(stock_data_resp.data)} stocks...")
    
    import time
    start_time = time.time()
    
    for i, stock_data in enumerate(stock_data_resp.data):
        ticker_raw = stock_data.ticker
        ticker = normalize_ticker(ticker_raw)
        ticker_out = ticker
        ticker_start = time.time()
        typer.echo(f"[{i+1}/{len(stock_data_resp.data)}] Scoring {ticker_raw} (as {ticker})...")
        
        # Drop nonexistent/delisted or obviously bad data (all key metrics zero)
        if ticker in {"TWTR"}:
            typer.echo(f"  [WARN] Dropping {ticker_raw} (delisted/nonexistent)")
            continue
        bad_metrics = False
        fund = stock_data.fundamentals
        if fund:
            # Check if all main metrics are zero (indicating bad/nonexistent data)
            metrics = [
                fund.revenue_ttm,
                fund.pe_ratio,
                fund.ev_ebitda,
                fund.fcf_margin_ttm,
                fund.operating_margin_ttm,
            ]
            # Filter out None values, then check if all remaining are zero
            non_none_metrics = [m for m in metrics if m is not None]
            if non_none_metrics and all(m == 0 for m in non_none_metrics):
                bad_metrics = True
        if bad_metrics:
            typer.echo(f"  [WARN] Dropping {ticker_raw} (all main metrics zero)")
            continue
        
        # Get sector/theme from candidates
        typer.echo(f"  -> Getting sector/theme info...")
        candidate = candidates_map.get(ticker) or candidates_map.get(ticker_raw)
        sector = candidate.sector if candidate else None
        theme = candidate.theme if candidate else None
        
        # Guard: if market cap missing, drop ticker (likely bad/nonexistent data)
        if not stock_data.price_data or stock_data.price_data.market_cap is None:
            typer.echo("  [WARN] Missing market_cap or price data; dropping ticker")
            continue

        # Calculate factor scores
        typer.echo(f"  -> Calculating factor scores...")
        factor_scores = FactorScores(
            value=calculate_value_score(stock_data.fundamentals),
            quality=calculate_quality_score(stock_data.fundamentals),
            growth=calculate_growth_score(stock_data.fundamentals),
            stability=calculate_stability_score(stock_data.price_data),
            revisions=calculate_revisions_score(stock_data.analyst_recommendations),
            momentum=calculate_momentum_score(stock_data.price_data),
        )
        value_str = f"{factor_scores.value:.2f}" if factor_scores.value is not None else 'N/A'
        quality_str = f"{factor_scores.quality:.2f}" if factor_scores.quality is not None else 'N/A'
        growth_str = f"{factor_scores.growth:.2f}" if factor_scores.growth is not None else 'N/A'
        momentum_str = f"{factor_scores.momentum:.2f}" if factor_scores.momentum is not None else 'N/A'
        typer.echo(f"  [OK] Factor scores: value={value_str}, quality={quality_str}, growth={growth_str}, momentum={momentum_str}")
        
        # Synthesize sentiment
        typer.echo(f"  -> Synthesizing sentiment (LLM call - this may take a moment)...")
        sentiment_start = time.time()
        sentiment = synthesize_sentiment(
            ticker,
            stock_data.analyst_recommendations,
            stock_data.news,
            stock_data.price_data,
            client,
            chosen_model,
            as_of_date=cfg.backtest_date if cfg.backtest_mode else None,
            model_cutoff=cfg.backtest_model_cutoff,
        )
        sentiment_elapsed = time.time() - sentiment_start
        typer.echo(f"  [OK] Sentiment: {sentiment.overall_sentiment} (score={sentiment.sentiment_score:.2f}, took {sentiment_elapsed:.1f}s)")
        
        # Apply risk screens
        typer.echo(f"  -> Applying risk screens...")
        risk_flags = apply_risk_screens(ticker, stock_data.price_data, cfg)
        typer.echo(f"  [OK] Risk checks: {'PASSED' if risk_flags.passed_all_checks else 'FAILED'} "
                   f"{'(' + ', '.join(risk_flags.failed_checks) + ')' if risk_flags.failed_checks else ''}")
        
        # Calculate composite score
        typer.echo(f"  -> Calculating composite score...")
        composite_score = calculate_composite_score(factor_scores, sentiment, risk_flags)
        typer.echo(f"  [OK] Composite score: {composite_score:.3f}")
        
        # Summarize news articles
        news_summary = None
        if stock_data.news:
            typer.echo(f"  -> Summarizing news articles...")
            news_summary_start = time.time()
            news_summary = summarize_news(
                ticker,
                stock_data.news,
                client,
                chosen_model,
            )
            news_summary_elapsed = time.time() - news_summary_start
            if news_summary:
                typer.echo(f"  [OK] News summary generated (took {news_summary_elapsed:.1f}s)")
            else:
                typer.echo(f"  [WARN] Could not generate news summary")
        
        scored_stock = ScoredStock(
            ticker=ticker_out,
            sector=sector,
            theme=theme,
            factor_scores=factor_scores,
            sentiment=sentiment,
            risk_flags=risk_flags,
            composite_score=composite_score,
            news_summary=news_summary,
            price=stock_data.price_data.price if stock_data.price_data else None,
            market_cap=stock_data.price_data.market_cap if stock_data.price_data else None,
        )
        
        scored_stocks.append(scored_stock)
        
        ticker_elapsed = time.time() - ticker_start
        typer.echo(f"  [OK] Completed {ticker} in {ticker_elapsed:.1f}s")
    
    # Sort by composite score (descending)
    scored_stocks.sort(key=lambda x: x.composite_score, reverse=True)
    
    # Calculate stats
    passed = [s for s in scored_stocks if s.risk_flags.passed_all_checks]
    stats = {
        "total_scored": len(scored_stocks),
        "passed_risk_screens": len(passed),
        "avg_composite_score": statistics.mean([s.composite_score for s in scored_stocks]),
        "top_10_scores": [s.composite_score for s in scored_stocks[:10]],
    }
    
    response = ScoredCandidatesResponse(candidates=scored_stocks, stats=stats)
    
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(response.model_dump_json(indent=2), encoding='utf-8')
    
    typer.echo(f"Scored {len(scored_stocks)} stocks -> {out}")
    typer.echo(f"  [OK] {len(passed)} passed risk screens")
    typer.echo(f"  [OK] Top 5 scores: {[f'{s.ticker}: {s.composite_score:.2f}' for s in scored_stocks[:5]]}")


def main():
    app()


if __name__ == "__main__":
    main()

