from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import typer
import yfinance as yf
from openai import OpenAI

from .config import load_config
from .openai_client import get_client, chat_json
from .data_apis import (
    fetch_analyst_recommendations_finnhub,
    fetch_news_finnhub,
    fetch_news_alpha_vantage,
    fetch_price_data_finnhub,
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


def fetch_price_data(ticker: str, finnhub_key: Optional[str] = None) -> Optional[PriceData]:
    """Fetch price and volume data using tiered approach: Finnhub -> yfinance."""
    # Tier 1: Try Finnhub API (free, reliable)
    if finnhub_key:
        result = fetch_price_data_finnhub(ticker, finnhub_key)
        if result:
            return result
    
    # Tier 2: Fallback to yfinance (may be blocked, but try anyway)
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


def fetch_fundamentals(ticker: str, fmp_key: Optional[str] = None) -> Optional[Fundamentals]:
    """Fetch fundamental data using FMP API."""
    if fmp_key:
        return fetch_fundamentals_fmp(ticker, fmp_key)
    return None


def fetch_analyst_recommendations_tiered(
    ticker: str,
    finnhub_key: Optional[str],
    client: OpenAI,
    model: str,
) -> Optional[AnalystRecommendation]:
    """Tiered approach: Finnhub -> LLM with web search."""
    # Tier 1: Try Finnhub API
    if finnhub_key:
        result = fetch_analyst_recommendations_finnhub(ticker, finnhub_key)
        if result:
            return result
    
    # Tier 2: LLM with web search
    system = """You are a financial data extraction assistant. Search the web for recent analyst recommendations 
    and extract structured data. Only extract information that is explicitly stated in search results."""
    user = f"""Search for recent analyst recommendations for {ticker}. Find and extract:
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
        return AnalystRecommendation(
            ticker=ticker,
            consensus=result.get("consensus"),
            price_target=result.get("price_target"),
            price_target_high=result.get("price_target_high"),
            price_target_low=result.get("price_target_low"),
            num_analysts=result.get("num_analysts"),
            recent_changes=result.get("recent_changes", []),
            as_of=date.today(),
        )
    except Exception as e:
        typer.echo(f"Error fetching analyst recommendations for {ticker}: {e}")
        return None


def fetch_news_tiered(
    ticker: str,
    finnhub_key: Optional[str],
    alpha_vantage_key: Optional[str],
    client: OpenAI,
    model: str,
    max_items: int = 10,
) -> list[NewsItem]:
    """Tiered approach: Finnhub -> Alpha Vantage -> LLM with web search."""
    news_items = []
    
    # Tier 1: Try Finnhub API
    if finnhub_key:
        finnhub_news = fetch_news_finnhub(ticker, finnhub_key, max_items)
        if finnhub_news:
            news_items.extend(finnhub_news)
            return news_items[:max_items]  # Return early if we got good data
    
    # Tier 2: Try Alpha Vantage (has sentiment built-in)
    if alpha_vantage_key and len(news_items) < max_items:
        alpha_news = fetch_news_alpha_vantage(
            ticker, alpha_vantage_key, max_items - len(news_items)
        )
        if alpha_news:
            news_items.extend(alpha_news)
            # Alpha Vantage already includes sentiment, so return
            return news_items[:max_items]
    
    # Tier 3: LLM with web search
    if len(news_items) < max_items:
        system = """You are a financial news extraction assistant. Search the web for recent news articles 
        and extract structured data. Only extract items that are explicitly found in search results."""
        user = f"""Search for recent news articles (last 30 days) about {ticker}. For each article, extract:
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
    model: Optional[str] = typer.Option(None, help="OpenAI model override"),
    skip_news: bool = typer.Option(False, help="Skip news fetching (faster)"),
    skip_analyst: bool = typer.Option(False, help="Skip analyst recommendations"),
    delay: float = typer.Option(0.5, help="Delay between API calls (seconds)"),
):
    """Fetch price, fundamentals, analyst recs, and news for all candidates."""
    cfg = load_config()
    chosen_model = model or cfg.openai_model
    
    if not candidates_file.exists():
        typer.echo(f"Candidates file not found: {candidates_file}")
        raise typer.Exit(code=1)
    
    candidates_data = json.loads(candidates_file.read_text())
    try:
        candidates_resp = CandidateResponse.model_validate(candidates_data)
    except Exception as e:
        typer.echo(f"Failed to parse candidates file: {e}")
        raise typer.Exit(code=1)
    
    client = get_client()
    stock_data_list = []
    
    typer.echo(f"Fetching data for {len(candidates_resp.candidates)} tickers...")
    
    for i, candidate in enumerate(candidates_resp.candidates):
        ticker = candidate.ticker
        typer.echo(f"[{i+1}/{len(candidates_resp.candidates)}] Processing {ticker}...")
        
        # Fetch price data (tiered: Finnhub -> yfinance)
        price_data = fetch_price_data(ticker, cfg.finnhub_api_key)
        time.sleep(delay)
        
        # Fetch fundamentals (using FMP API)
        if cfg.fmp_api_key:
            typer.echo(f"  -> Fetching fundamentals from FMP API...")
            fundamentals = fetch_fundamentals(ticker, cfg.fmp_api_key)
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
                ticker, cfg.finnhub_api_key, client, chosen_model
            )
            time.sleep(delay)
        
        # Fetch news (tiered: Finnhub -> Alpha Vantage -> LLM web search)
        news_items = []
        if not skip_news:
            news_items = fetch_news_tiered(
                ticker,
                cfg.finnhub_api_key,
                cfg.alpha_vantage_api_key,
                client,
                chosen_model,
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
        
        stock_data_list.append(stock_data)
    
    response = StockDataResponse(data=stock_data_list)
    
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    # Write with UTF-8 encoding to handle Unicode characters (e.g., in news headlines)
    out.write_text(response.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Fetched data for {len(stock_data_list)} tickers -> {out}")


def main():
    app()


if __name__ == "__main__":
    main()

