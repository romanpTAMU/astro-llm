from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import typer
import yfinance as yf
from openai import OpenAI

from .config import load_config
from .openai_client import get_client, chat_json
from .data_apis import (
    fetch_analyst_recommendations_finnhub,
    fetch_news_finnhub,
    fetch_news_fmp,
    fetch_news_alpha_vantage,
    fetch_price_data_finnhub,
    fetch_price_data_fmp,
    fetch_fundamentals_fmp,
)
from .models import (
    CandidateResponse,
    StockData,
    StockDataResponse,
    PriceData,
    Fundamentals,
    AnalystRecommendation,
    NewsItem,
)

app = typer.Typer(add_completion=False)


def fetch_price_data(ticker: str, finnhub_key: Optional[str] = None, fmp_key: Optional[str] = None, as_of_date: Optional[date] = None) -> Optional[PriceData]:
    """Fetch price and volume data using tiered approach: Finnhub -> FMP -> yfinance.
    
    Args:
        ticker: Stock ticker
        finnhub_key: Finnhub API key (optional)
        fmp_key: FMP API key (optional)
        as_of_date: If provided, fetch historical price for this date (for backtesting)
    """
    # In backtest mode, use historical price from FMP
    if as_of_date and fmp_key:
        from .performance_tracker import fetch_historical_price
        hist_price = fetch_historical_price(ticker, as_of_date, fmp_key)
        if hist_price:
            # Create PriceData from historical price
            return PriceData(
                ticker=ticker,
                price=hist_price,
                volume=0,  # Historical volume not critical
                avg_volume_30d=None,
                market_cap=None,
                price_change_pct=None,
                as_of=datetime.combine(as_of_date, datetime.min.time()),
            )
    
    # Tier 1: Try Finnhub API (free, reliable)
    if finnhub_key:
        result = fetch_price_data_finnhub(ticker, finnhub_key)
        if result:
            # Enrich with avg_volume_30d from FMP if available
            if fmp_key and result.avg_volume_30d is None:
                try:
                    to_date = date.today()
                    from_date = to_date - timedelta(days=30)
                    hist_url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
                    hist_params = {"apikey": fmp_key, "from": from_date.strftime("%Y-%m-%d"), "to": to_date.strftime("%Y-%m-%d")}
                    hist_resp = requests.get(hist_url, params=hist_params, timeout=10)
                    if hist_resp.status_code == 200:
                        hist_data = hist_resp.json()
                        if isinstance(hist_data, dict) and "historical" in hist_data:
                            volumes = [day.get("volume", 0) for day in hist_data["historical"] if day.get("volume")]
                            if volumes:
                                result.avg_volume_30d = int(sum(volumes) / len(volumes))
                except Exception:
                    pass  # avg_volume_30d is optional
            return result
    
    # Tier 2: Try FMP Quote API
    if fmp_key:
        result = fetch_price_data_fmp(ticker, fmp_key)
        if result:
            return result
    
    # Tier 3: Fallback to yfinance (may be blocked, but try anyway)
    try:
        stock = yf.Ticker(ticker)
        
        # Get current quote - use history as it's more reliable than info
        hist = stock.history(period="5d")
        if hist is None or hist.empty:
            typer.echo(f"  [WARN] No price data available for {ticker}")
            return None
        
        latest = hist.iloc[-1]
        avg_volume = int(hist["Volume"].mean()) if len(hist) > 0 else None
        
        price = float(latest["Close"])
        volume = int(latest["Volume"])
        
        # Calculate price change
        if len(hist) > 1:
            prev_close = float(hist.iloc[-2]["Close"])
            price_change_pct = ((price - prev_close) / prev_close) * 100
        else:
            price_change_pct = None
        
        # Try to get market cap from info, but don't fail if unavailable
        market_cap = None
        try:
            info = stock.info
            if info and isinstance(info, dict):
                market_cap = info.get("marketCap")
        except Exception:
            pass  # Info might be blocked, continue without it
        
        return PriceData(
            ticker=ticker,
            price=price,
            volume=volume,
            avg_volume_30d=avg_volume,
            market_cap=float(market_cap) if market_cap else None,
            price_change_pct=price_change_pct,
            as_of=datetime.now(),
        )
    except Exception as e:
        typer.echo(f"  [ERROR] Error fetching price data for {ticker}: {e}")
        return None


def fetch_fundamentals(ticker: str, fmp_key: Optional[str] = None, as_of_date: Optional[date] = None) -> Optional[Fundamentals]:
    """Fetch fundamental data using FMP API.
    
    Args:
        ticker: Stock ticker
        fmp_key: FMP API key
        as_of_date: If provided, fetch fundamentals as of this date (for backtesting)
    """
    if fmp_key:
        return fetch_fundamentals_fmp(ticker, fmp_key, as_of_date)
    return None


def fetch_analyst_recommendations_tiered(
    ticker: str,
    finnhub_key: Optional[str],
    fmp_key: Optional[str],
    client: OpenAI,
    model: str,
    as_of_date: Optional[date] = None,
    disable_web_search: bool = False,
) -> Optional[AnalystRecommendation]:
    """Tiered approach: Finnhub -> LLM with web search (web search disabled in backtest mode)."""
    # Tier 1: Try Finnhub API
    if finnhub_key:
        result = fetch_analyst_recommendations_finnhub(ticker, finnhub_key)
        if result:
            # Enrich price targets via FMP if available
            if fmp_key:
                from .data_apis import fetch_price_targets_fmp
                pt_mean, pt_high, pt_low = fetch_price_targets_fmp(ticker, fmp_key)
                result.price_target = pt_mean if pt_mean is not None else result.price_target
                result.price_target_high = pt_high if pt_high is not None else result.price_target_high
                result.price_target_low = pt_low if pt_low is not None else result.price_target_low
            return result
    
    # Tier 2: LLM with web search (disabled in backtest mode)
    if disable_web_search:
        # In backtest mode, don't use web search - return None if Finnhub didn't work
        return None
    
    date_context = f" as of {as_of_date.strftime('%Y-%m-%d')}" if as_of_date else ""
    system = """You are a financial data extraction assistant. Search the web for recent analyst recommendations 
    and extract structured data. Only extract information that is explicitly stated in search results."""
    user = f"""Search for recent analyst recommendations for {ticker}{date_context}. Find and extract:
- Consensus rating (Buy/Hold/Sell)
- Average price target
- High and low price targets
- Number of analysts covering
- Recent rating changes (upgrades/downgrades) in the past 30 days

Output JSON:
{{
  "consensus": "Buy" or "Hold" or "Sell" or null,
  "price_target": number or null,
  "price_target_high": number or null,
  "price_target_low": number or null,
  "num_analysts": number or null,
  "recent_changes": ["Upgrade by Bank A", "Downgrade by Bank B"] or []
}}"""

    try:
        result = chat_json(client, model, system, user, use_web_search=True)
        # Use as_of_date if provided (for backtesting), otherwise use today
        effective_as_of = as_of_date if as_of_date else date.today()
        
        rec = AnalystRecommendation(
            ticker=ticker,
            consensus=result.get("consensus"),
            price_target=result.get("price_target"),
            price_target_high=result.get("price_target_high"),
            price_target_low=result.get("price_target_low"),
            num_analysts=result.get("num_analysts"),
            recent_changes=result.get("recent_changes", []),
            as_of=effective_as_of,
        )
        # Enrich price targets via FMP if available
        if fmp_key:
            from .data_apis import fetch_price_targets_fmp
            pt_mean, pt_high, pt_low = fetch_price_targets_fmp(ticker, fmp_key)
            rec.price_target = pt_mean if pt_mean is not None else rec.price_target
            rec.price_target_high = pt_high if pt_high is not None else rec.price_target_high
            rec.price_target_low = pt_low if pt_low is not None else rec.price_target_low
        return rec
    except Exception as e:
        typer.echo(f"Error fetching analyst recommendations for {ticker}: {e}")
        return None


def fetch_news_tiered(
    ticker: str,
    finnhub_key: Optional[str],
    alpha_vantage_key: Optional[str],
    fmp_key: Optional[str],
    client: OpenAI,
    model: str,
    max_items: int = 10,
    as_of_date: Optional[date] = None,
    disable_web_search: bool = False,
) -> list[NewsItem]:
    """Tiered approach: Finnhub -> FMP -> Alpha Vantage -> LLM with web search."""
    news_items = []
    
    # Tier 1: Try Finnhub API
    if finnhub_key:
        finnhub_news = fetch_news_finnhub(ticker, finnhub_key, max_items, as_of_date)
        # Filter by date if in backtest mode
        if as_of_date:
            finnhub_news = [
                n for n in finnhub_news 
                if n.published_at and n.published_at.date() <= as_of_date
            ]
        if finnhub_news:
            news_items.extend(finnhub_news)
            news_items = sorted(news_items, key=lambda x: x.published_at or datetime.min, reverse=True)
            if len(news_items) >= max_items:
                return news_items[:max_items]  # Return early if we got enough data
    
    # Tier 2: Try FMP General News API (supports historical dates)
    if fmp_key and len(news_items) < max_items:
        fmp_news = fetch_news_fmp(ticker, fmp_key, max_items - len(news_items), as_of_date)
        # Filter by date if in backtest mode (already done in fetch_news_fmp, but double-check)
        if as_of_date:
            fmp_news = [
                n for n in fmp_news 
                if n.published_at and n.published_at.date() <= as_of_date
            ]
        if fmp_news:
            news_items.extend(fmp_news)
            news_items = sorted(news_items, key=lambda x: x.published_at or datetime.min, reverse=True)
            if len(news_items) >= max_items:
                return news_items[:max_items]
    
    # Tier 3: Try Alpha Vantage (has sentiment built-in)
    if alpha_vantage_key and len(news_items) < max_items:
        alpha_news = fetch_news_alpha_vantage(
            ticker, alpha_vantage_key, max_items - len(news_items), as_of_date
        )
        # Filter by date if in backtest mode
        if as_of_date:
            alpha_news = [
                n for n in alpha_news 
                if n.published_at and n.published_at.date() <= as_of_date
            ]
        if alpha_news:
            news_items.extend(alpha_news)
            news_items = sorted(news_items, key=lambda x: x.published_at or datetime.min, reverse=True)
            if len(news_items) >= max_items:
                return news_items[:max_items]
    
    # Tier 4: LLM with web search (disabled in backtest mode)
    if len(news_items) < max_items and not disable_web_search:
        date_context = f" up to {as_of_date.strftime('%Y-%m-%d')}" if as_of_date else ""
        system = """You are a financial news extraction assistant. Search the web for recent news articles 
        and extract structured data. Only extract items that are explicitly found in search results."""
        user = f"""Search for recent news articles (last 30 days{date_context}) about {ticker}. For each article, extract:
- headline
- summary (1-2 sentences)
- source
- url (if available)
- published date (if available)

Output JSON array:
{{
  "news": [
    {{
      "headline": "...",
      "summary": "...",
      "source": "...",
      "url": "..." or null,
      "published_at": "YYYY-MM-DD" or null
    }}
  ]
}}

Limit to {max_items - len(news_items)} most relevant articles. If you don't find recent news, return empty array."""
        
        try:
            result = chat_json(client, model, system, user, use_web_search=True)
            for item in result.get("news", []):
                published_at = None
                if item.get("published_at"):
                    try:
                        published_at = datetime.strptime(item["published_at"], "%Y-%m-%d")
                    except:
                        pass
                
                # Filter by date in backtest mode
                if as_of_date and published_at and published_at.date() > as_of_date:
                    continue
                
                news_items.append(
                    NewsItem(
                        ticker=ticker,
                        headline=item.get("headline", ""),
                        summary=item.get("summary"),
                        source=item.get("source", "Unknown"),
                        url=item.get("url"),
                        published_at=published_at,
                    )
                )
        except Exception as e:
            typer.echo(f"Error fetching news via LLM for {ticker}: {e}")
    
    return news_items[:max_items]


def classify_news_sentiment(
    news_items: list[NewsItem], client: OpenAI, model: str
) -> list[NewsItem]:
    """Classify sentiment for news items using LLM."""
    if not news_items:
        return news_items
    
    system = """Classify the sentiment of each news item as bullish, neutral, or bearish based on the headline and summary."""
    user = f"""Classify sentiment for these news items:
{json.dumps([{"headline": n.headline, "summary": n.summary} for n in news_items], indent=2)}

Output JSON:
{{
  "sentiments": ["bullish", "neutral", "bearish", ...]
}}"""
    
    try:
        result = chat_json(client, model, system, user)
        sentiments = result.get("sentiments", [])
        for i, item in enumerate(news_items):
            if i < len(sentiments):
                item.sentiment = sentiments[i]
    except Exception as e:
        typer.echo(f"Error classifying sentiment: {e}")
    
    return news_items


@app.command()
def fetch(
    candidates_file: Path = typer.Option(
        Path("data/candidates.json"), help="Input candidates JSON path"
    ),
    out: Path = typer.Option(
        Path("data/stock_data.json"), help="Output JSON path"
    ),
    model: Optional[str] = typer.Option(None, help="OpenAI model override (defaults to cheap_model for efficiency)"),
    skip_news: bool = typer.Option(False, help="Skip news fetching (faster)"),
    skip_analyst: bool = typer.Option(False, help="Skip analyst recommendations"),
    delay: float = typer.Option(0.5, help="Delay between API calls (seconds)"),
    resume: bool = typer.Option(False, help="Resume mode: only fetch missing tickers from existing output file"),
    fix_sentiment_only: bool = typer.Option(False, help="Only fix missing news sentiment (don't re-fetch other data)"),
):
    """Fetch price, fundamentals, analyst recs, and news for all candidates."""
    cfg = load_config()
    # Use cheap model by default for news classification (high volume, simple task)
    chosen_model = model or cfg.cheap_model
    
    if not candidates_file.exists():
        typer.echo(f"Candidates file not found: {candidates_file}")
        raise typer.Exit(code=1)
    
    candidates_data = json.loads(candidates_file.read_text())
    try:
        candidates_resp = CandidateResponse.model_validate(candidates_data)
    except Exception as e:
        typer.echo(f"Failed to parse candidates file: {e}")
        raise typer.Exit(code=1)
    
    # Load existing data if resume mode and file exists
    existing_data_map = {}
    if resume and out.exists():
        try:
            existing_text = out.read_text(encoding='utf-8')
            if existing_text and existing_text.strip():
                existing_data = json.loads(existing_text)
                existing_resp = StockDataResponse.model_validate(existing_data)
                existing_data_map = {item.ticker: item for item in existing_resp.data}
                typer.echo(f"Resume mode: Found {len(existing_data_map)} existing tickers in {out}")
        except Exception as e:
            typer.echo(f"[WARN] Could not load existing data for resume: {e}")
            typer.echo("Continuing with fresh fetch...")
    
    # Determine which tickers need to be fetched
    all_tickers = {c.ticker for c in candidates_resp.candidates}
    if fix_sentiment_only:
        # Only fix sentiment for existing entries
        tickers_to_fetch = []
        sentiment_tickers = []
        for ticker, stock_data in existing_data_map.items():
            if stock_data.news:
                news_without_sentiment = [n for n in stock_data.news if not n.sentiment]
                if len(news_without_sentiment) > 0:
                    sentiment_tickers.append(ticker)
        typer.echo(f"Sentiment fix mode: Found {len(sentiment_tickers)} tickers with news missing sentiment")
        if sentiment_tickers:
            typer.echo(f"  Tickers needing sentiment: {', '.join(sorted(sentiment_tickers))}")
    elif resume:
        # Check for missing tickers
        missing_tickers = all_tickers - set(existing_data_map.keys())
        
        # Check for incomplete data (missing fundamentals or news without sentiment)
        incomplete_tickers = set()
        for ticker, stock_data in existing_data_map.items():
            is_incomplete = False
            
            # Check if fundamentals are missing key metrics
            if stock_data.fundamentals:
                fund = stock_data.fundamentals
                if fund.roic is None or fund.net_debt_to_ebitda is None or fund.pe_ratio is None or fund.ev_ebitda is None:
                    is_incomplete = True
            else:
                # Fundamentals completely missing
                is_incomplete = True
            
            # Check if news items are missing sentiment
            if stock_data.news:
                news_without_sentiment = [n for n in stock_data.news if not n.sentiment]
                if len(news_without_sentiment) > 0:
                    is_incomplete = True
            
            if is_incomplete:
                incomplete_tickers.add(ticker)
        
        # Combine missing and incomplete
        tickers_to_fetch = [c for c in candidates_resp.candidates 
                           if c.ticker in missing_tickers or c.ticker in incomplete_tickers]
        
        typer.echo(f"Resume mode: {len(missing_tickers)} tickers missing, {len(incomplete_tickers)} tickers incomplete, {len(existing_data_map)} already fetched")
        if incomplete_tickers:
            typer.echo(f"  Incomplete tickers: {', '.join(sorted(incomplete_tickers))}")
    else:
        tickers_to_fetch = candidates_resp.candidates
    
    client = get_client()
    new_stock_data_list = []  # Only newly fetched data
    
    # Handle sentiment-only fix mode
    if fix_sentiment_only:
        if not sentiment_tickers:
            typer.echo("No tickers found with missing sentiment. All news items already have sentiment classified.")
            return
        
        typer.echo(f"Fixing sentiment for {len(sentiment_tickers)} tickers...")
        for ticker in sentiment_tickers:
            stock_data = existing_data_map[ticker]
            if stock_data.news:
                news_needing_sentiment = [n for n in stock_data.news if not n.sentiment]
                if news_needing_sentiment:
                    typer.echo(f"  Classifying sentiment for {ticker} ({len(news_needing_sentiment)} items)...")
                    classify_news_sentiment(news_needing_sentiment, client, chosen_model)
                    time.sleep(delay)
        # Write updated data
        response = StockDataResponse(data=list(existing_data_map.values()))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(response.model_dump_json(indent=2), encoding='utf-8')
        typer.echo(f"Fixed sentiment for {len(sentiment_tickers)} tickers -> {out}")
        return
    
    typer.echo(f"Fetching data for {len(tickers_to_fetch)} tickers...")
    
    for i, candidate in enumerate(tickers_to_fetch):
        ticker = candidate.ticker
        total_count = len(candidates_resp.candidates)
        current_num = len(existing_data_map) + i + 1 if resume else i + 1
        typer.echo(f"[{current_num}/{total_count}] Processing {ticker}...")
        
        # Fetch price data (tiered: Finnhub -> FMP -> yfinance)
        # In backtest mode, use historical price
        price_data = fetch_price_data(
            ticker, 
            cfg.finnhub_api_key, 
            cfg.fmp_api_key,
            as_of_date=cfg.backtest_date if cfg.backtest_mode else None,
        )
        time.sleep(delay)
        
        # Fetch fundamentals (using FMP API)
        if cfg.fmp_api_key:
            typer.echo(f"  -> Fetching fundamentals from FMP API...")
            fundamentals = fetch_fundamentals(ticker, cfg.fmp_api_key, as_of_date=cfg.backtest_date if cfg.backtest_mode else None)
            if fundamentals:
                typer.echo(f"  [OK] Fundamentals fetched")
            else:
                typer.echo(f"  [WARN] Fundamentals not available (API returned no data)")
        else:
            typer.echo(f"  [WARN] Skipping fundamentals (FMP_API_KEY not set)")
            fundamentals = None
        time.sleep(delay)
        
        # Fetch analyst recommendations (tiered: Finnhub -> LLM web search)
        analyst_recs = None
        if not skip_analyst:
            analyst_recs = fetch_analyst_recommendations_tiered(
                ticker, 
                cfg.finnhub_api_key, 
                cfg.fmp_api_key, 
                client, 
                chosen_model,
                as_of_date=cfg.backtest_date if cfg.backtest_mode else None,
                disable_web_search=cfg.backtest_mode,
            )
            time.sleep(delay)
        
        # Fetch news (tiered: Finnhub -> FMP -> Alpha Vantage -> LLM web search)
        news_items = []
        if not skip_news:
            news_items = fetch_news_tiered(
                ticker,
                cfg.finnhub_api_key,
                cfg.alpha_vantage_api_key,
                cfg.fmp_api_key,
                client,
                chosen_model,
                as_of_date=cfg.backtest_date if cfg.backtest_mode else None,
                disable_web_search=cfg.backtest_mode,
            )
            time.sleep(delay)
            # Classify sentiment for items that don't have it (from LLM search)
            items_needing_sentiment = [n for n in news_items if not n.sentiment]
            if items_needing_sentiment:
                classify_news_sentiment(items_needing_sentiment, client, chosen_model)
                time.sleep(delay)
        
        stock_data = StockData(
            ticker=ticker,
            price_data=price_data,
            fundamentals=fundamentals,
            analyst_recommendations=analyst_recs,
            news=news_items,
        )
        
        new_stock_data_list.append(stock_data)
    
    # Merge: existing + newly fetched
    if resume:
        final_data_map = existing_data_map.copy()
        for new_data in new_stock_data_list:
            # If existing entry exists, merge/update it properly
            if new_data.ticker in final_data_map:
                existing = final_data_map[new_data.ticker]
                # Update fundamentals if new ones are better (more complete)
                if new_data.fundamentals and (not existing.fundamentals or 
                    (existing.fundamentals and (
                        existing.fundamentals.roic is None or
                        existing.fundamentals.net_debt_to_ebitda is None or
                        existing.fundamentals.pe_ratio is None or
                        existing.fundamentals.ev_ebitda is None
                    ))):
                    # Use new fundamentals if they're more complete
                    existing.fundamentals = new_data.fundamentals
                
                # Update news sentiment for items that were missing it
                if existing.news:
                    # Check if any existing news items are missing sentiment
                    news_needing_sentiment = [n for n in existing.news if not n.sentiment]
                    if news_needing_sentiment:
                        # Classify existing news items that don't have sentiment
                        classify_news_sentiment(news_needing_sentiment, client, chosen_model)
                        time.sleep(delay)
                    
                    # If new data has news, merge by matching headlines/URLs and updating sentiment
                    if new_data.news:
                        existing_news_map = {(n.headline, n.url or ""): n for n in existing.news}
                        for new_news in new_data.news:
                            key = (new_news.headline, new_news.url or "")
                            if key in existing_news_map and not existing_news_map[key].sentiment and new_news.sentiment:
                                existing_news_map[key].sentiment = new_news.sentiment
                        existing.news = list(existing_news_map.values())
                elif new_data.news:
                    existing.news = new_data.news
                
                # Update other fields if missing
                if not existing.price_data and new_data.price_data:
                    existing.price_data = new_data.price_data
                if not existing.analyst_recommendations and new_data.analyst_recommendations:
                    existing.analyst_recommendations = new_data.analyst_recommendations
            else:
                final_data_map[new_data.ticker] = new_data  # Add new
        stock_data_list = list(final_data_map.values())
    else:
        stock_data_list = new_stock_data_list
    
    response = StockDataResponse(data=stock_data_list)
    
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    # Write with UTF-8 encoding to handle Unicode characters (e.g., in news headlines)
    out.write_text(response.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Fetched data for {len(stock_data_list)} tickers -> {out}")


def main():
    app()


if __name__ == "__main__":
    main()

