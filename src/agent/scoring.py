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


def calculate_value_score(fundamentals) -> Optional[float]:
    """Calculate value factor score from EV/EBITDA, FCF yield, P/E."""
    if not fundamentals:
        return None
    
    scores = []
    
    # EV/EBITDA (lower is better for value)
    if fundamentals.ev_ebitda is not None:
        # Inverse: lower EV/EBITDA = higher value score
        # Normalize: assume 10-20 is typical, <10 is good value, >20 is expensive
        if fundamentals.ev_ebitda < 10:
            scores.append(1.0)
        elif fundamentals.ev_ebitda < 15:
            scores.append(0.5)
        elif fundamentals.ev_ebitda < 20:
            scores.append(0.0)
        else:
            scores.append(-0.5)
    
    # P/E ratio (lower is better for value)
    if fundamentals.pe_ratio is not None and fundamentals.pe_ratio > 0:
        # Inverse: lower P/E = higher value score
        if fundamentals.pe_ratio < 15:
            scores.append(1.0)
        elif fundamentals.pe_ratio < 20:
            scores.append(0.5)
        elif fundamentals.pe_ratio < 30:
            scores.append(0.0)
        else:
            scores.append(-0.5)
    
    # FCF yield (higher is better for value)
    if fundamentals.fcf_margin_ttm is not None:
        # Higher FCF margin = better value
        if fundamentals.fcf_margin_ttm > 15:
            scores.append(1.0)
        elif fundamentals.fcf_margin_ttm > 10:
            scores.append(0.5)
        elif fundamentals.fcf_margin_ttm > 5:
            scores.append(0.0)
        else:
            scores.append(-0.3)
    
    return statistics.mean(scores) if scores else None


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
    """Calculate stability factor score (placeholder - would need historical volatility data)."""
    # For now, we'll use a simple heuristic based on price change
    if not price_data or price_data.price_change_pct is None:
        return None
    
    # Lower absolute price change = more stable (but this is simplistic)
    abs_change = abs(price_data.price_change_pct)
    
    if abs_change < 2:
        return 0.5  # Stable
    elif abs_change < 5:
        return 0.0  # Moderate volatility
    else:
        return -0.3  # High volatility


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


def synthesize_sentiment(
    ticker: str,
    analyst_recs,
    news_items: list,
    price_data,
    client: OpenAI,
    model: str,
) -> SentimentAnalysis:
    """Synthesize sentiment from analyst recs and news using LLM."""
    
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
    
    news_summary = []
    bullish_count = 0
    bearish_count = 0
    neutral_count = 0
    
    for news in news_items[:10]:  # Top 10 news items
        sentiment = news.sentiment or "neutral"
        if sentiment == "bullish":
            bullish_count += 1
        elif sentiment == "bearish":
            bearish_count += 1
        else:
            neutral_count += 1
        
        news_summary.append(f"- {news.headline} ({sentiment})")
    
    system = """You are a financial sentiment analyst. Synthesize sentiment from analyst recommendations and news.
    Output JSON only."""
    
    user = f"""Ticker: {ticker}
    
Analyst Information:
{chr(10).join(analyst_info) if analyst_info else 'No analyst data available'}

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
            if current_price > 0:
                price_target_upside = ((analyst_recs.price_target - current_price) / current_price) * 100
        
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
    
    # Liquidity check
    liquidity_ok = True
    if price_data:
        # Check if volume meets minimum dollar volume
        if price_data.volume and price_data.price:
            dollar_volume = price_data.volume * price_data.price
            if dollar_volume < cfg.min_avg_dollar_volume:
                liquidity_ok = False
                failed_checks.append(f"Low liquidity (${dollar_volume:,.0f} < ${cfg.min_avg_dollar_volume:,})")
    else:
        liquidity_ok = False
        failed_checks.append("No price data available")
    
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
    
    # Add sentiment score
    score += sentiment.sentiment_score * weights["sentiment"]
    weight_sum += weights["sentiment"]
    
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
        Path("data/candidates.json"), help="Original candidates file for sector/theme info"
    ),
    out: Path = typer.Option(
        Path("data/scored_candidates.json"), help="Output JSON path"
    ),
    model: Optional[str] = typer.Option(None, help="OpenAI model override"),
):
    """Score candidates using factor analysis, sentiment synthesis, and risk screens."""
    cfg = load_config()
    chosen_model = model or cfg.openai_model
    
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
    
    # Load candidates for sector/theme info
    candidates_map = {}
    if candidates_file.exists():
        candidates_text = candidates_file.read_text(encoding='utf-8')
        if candidates_text and candidates_text.strip():
            try:
                candidates_json = json.loads(candidates_text)
                from .models import CandidateResponse
                candidates_resp = CandidateResponse.model_validate(candidates_json)
                candidates_map = {c.ticker: c for c in candidates_resp.candidates}
            except Exception as e:
                typer.echo(f"  [WARN] Could not load candidates file: {e}")
                typer.echo("  Continuing without sector/theme info...")
    
    client = get_client()
    scored_stocks = []
    
    typer.echo(f"Scoring {len(stock_data_resp.data)} stocks...")
    
    import time
    start_time = time.time()
    
    for i, stock_data in enumerate(stock_data_resp.data):
        ticker = stock_data.ticker
        ticker_start = time.time()
        typer.echo(f"[{i+1}/{len(stock_data_resp.data)}] Scoring {ticker}...")
        
        # Get sector/theme from candidates
        typer.echo(f"  -> Getting sector/theme info...")
        candidate = candidates_map.get(ticker)
        sector = candidate.sector if candidate else None
        theme = candidate.theme if candidate else None
        
        # Calculate factor scores
        typer.echo(f"  -> Calculating factor scores...")
        factor_scores = FactorScores(
            value=calculate_value_score(stock_data.fundamentals),
            quality=calculate_quality_score(stock_data.fundamentals),
            growth=calculate_growth_score(stock_data.fundamentals),
            stability=calculate_stability_score(stock_data.price_data),
            revisions=calculate_revisions_score(stock_data.analyst_recommendations),
        )
        typer.echo(f"  [OK] Factor scores: value={factor_scores.value:.2f if factor_scores.value else 'N/A'}, "
                   f"quality={factor_scores.quality:.2f if factor_scores.quality else 'N/A'}, "
                   f"growth={factor_scores.growth:.2f if factor_scores.growth else 'N/A'}")
        
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
        
        scored_stock = ScoredStock(
            ticker=ticker,
            sector=sector,
            theme=theme,
            factor_scores=factor_scores,
            sentiment=sentiment,
            risk_flags=risk_flags,
            composite_score=composite_score,
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

