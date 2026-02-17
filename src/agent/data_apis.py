from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import requests
import typer

from .models import AnalystRecommendation, NewsItem, PriceData, Fundamentals


def _fetch_price_target_consensus(ticker: str, api_key: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Call FMP price-target-consensus endpoint."""
    try:
        url = "https://financialmodelingprep.com/stable/price-target-consensus"
        params = {"symbol": ticker, "apikey": api_key}
        resp = requests.get(url, params=params, timeout=(5, 30))
        if resp.status_code != 200:
            return (None, None, None)
        data = resp.json()
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            return (
                data[0].get("targetConsensus"),
                data[0].get("targetHigh"),
                data[0].get("targetLow"),
            )
        return (None, None, None)
    except Exception:
        return (None, None, None)


def _fetch_price_target_summary(ticker: str, api_key: str) -> Optional[float]:
    """Call FMP price-target-summary endpoint and return a stable average (lastYear > allTime > lastQuarter > lastMonth)."""
    try:
        url = "https://financialmodelingprep.com/stable/price-target-summary"
        params = {"symbol": ticker, "apikey": api_key}
        resp = requests.get(url, params=params, timeout=(5, 30))
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not (isinstance(data, list) and data and isinstance(data[0], dict)):
            return None
        entry = data[0]
        for key in (
            "lastYearAvgPriceTarget",
            "allTimeAvgPriceTarget",
            "lastQuarterAvgPriceTarget",
            "lastMonthAvgPriceTarget",
        ):
            if entry.get(key) is not None:
                return entry.get(key)
        return None
    except Exception:
        return None


def _fetch_latest_split_ratio(ticker: str, api_key: str) -> Optional[float]:
    """Fetch latest split ratio (numerator/denominator)."""
    try:
        url = "https://financialmodelingprep.com/stable/splits"
        params = {"symbol": ticker, "limit": 1, "apikey": api_key}
        resp = requests.get(url, params=params, timeout=(5, 30))
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not (isinstance(data, list) and data and isinstance(data[0], dict)):
            return None
        entry = data[0]
        num = entry.get("numerator")
        den = entry.get("denominator")
        if num and den and den != 0:
            return num / den
        return None
    except Exception:
        return None


def fetch_price_targets_fmp(
    ticker: str,
    api_key: str,
    current_price: Optional[float] = None,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Fetch price targets from FMP with extra guards.
    
    - Uses consensus endpoint.
    - Falls back to summary endpoint averages.
    - Adjusts for recent splits if detected.
    - If consensus is an extreme outlier vs price, prefer summary average.
    """
    target_mean, target_high, target_low = _fetch_price_target_consensus(ticker, api_key)
    summary_avg = _fetch_price_target_summary(ticker, api_key)
    split_ratio = _fetch_latest_split_ratio(ticker, api_key)

    # Adjust for splits if target looks unadjusted
    if (
        target_mean
        and current_price
        and current_price > 0
        and split_ratio
        and target_mean / current_price > split_ratio * 1.5
    ):
        adjusted = target_mean / split_ratio
        typer.echo(
            f"[WARN] Adjusting price target for {ticker} by split ratio {split_ratio} "
            f"({target_mean} -> {adjusted})"
        )
        target_mean = adjusted
        if target_high:
            target_high = target_high / split_ratio
        if target_low:
            target_low = target_low / split_ratio

    # If consensus is an extreme outlier, prefer summary if available
    if (
        target_mean
        and current_price
        and current_price > 0
        and target_mean / current_price > 6
        and summary_avg
        and summary_avg / current_price < 6
    ):
        typer.echo(
            f"[WARN] Consensus target for {ticker} looks extreme; using summary avg {summary_avg} instead of {target_mean}"
        )
        target_mean = summary_avg

    # Fallback to summary if consensus missing
    if target_mean is None and summary_avg is not None:
        target_mean = summary_avg

    return (target_mean, target_high, target_low)


def fetch_analyst_recommendations_finnhub(
    ticker: str, api_key: str
) -> Optional[AnalystRecommendation]:
    """Fetch analyst recommendations from Finnhub API."""
    try:
        url = "https://finnhub.io/api/v1/stock/recommendation"
        params = {"symbol": ticker, "token": api_key}
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data:
            return None
        
        # Get most recent recommendation
        latest = data[0] if data else None
        if not latest:
            return None
        
        # Calculate consensus from last period
        buy = latest.get("buy", 0)
        hold = latest.get("hold", 0)
        sell = latest.get("sell", 0)
        total = buy + hold + sell
        
        if total == 0:
            return None
        
        # Determine consensus with stronger thresholds to account for analyst bias
        # Require at least 40% for Buy, 30% for Sell (to avoid weak consensus)
        buy_pct = buy / total
        hold_pct = hold / total
        sell_pct = sell / total
        
        if buy_pct >= 0.40 and buy > hold and buy > sell:
            consensus = "Buy"
        elif sell_pct >= 0.30 and sell > buy and sell > hold:
            consensus = "Sell"
        elif hold_pct >= 0.50 or (hold > buy and hold > sell):
            consensus = "Hold"
        elif buy_pct > sell_pct:
            consensus = "Buy"  # Weak buy if buy > sell but below threshold
        else:
            consensus = "Hold"  # Default to hold for unclear cases
        
        # Price targets will be optionally enriched via FMP in the tiered fetcher
        price_target = None
        price_target_high = None
        price_target_low = None
        
        return AnalystRecommendation(
            ticker=ticker,
            consensus=consensus,
            buy_count=buy,
            hold_count=hold,
            sell_count=sell,
            price_target=price_target,
            price_target_high=price_target_high,
            price_target_low=price_target_low,
            num_analysts=total,
            recent_changes=[],  # Would need to compare historical data
            as_of=date.today(),  # Note: Finnhub API returns current recommendations, not historical
        )
    except Exception as e:
        typer.echo(f"Finnhub API error for {ticker}: {e}")
        return None


def fetch_news_finnhub(
    ticker: str, api_key: str, max_items: int = 10, as_of_date: Optional[date] = None
) -> list[NewsItem]:
    """Fetch news from Finnhub API.
    
    Args:
        ticker: Stock ticker
        api_key: Finnhub API key
        max_items: Maximum number of news items to return
        as_of_date: If provided, only fetch news up to this date (for backtesting)
    """
    try:
        # Get company news from past 30 days (or up to as_of_date)
        url = "https://finnhub.io/api/v1/company-news"
        effective_date = as_of_date if as_of_date else date.today()
        to_date = effective_date.strftime("%Y-%m-%d")
        from_date = (effective_date - timedelta(days=30)).strftime("%Y-%m-%d")
        
        params = {
            "symbol": ticker,
            "from": from_date,
            "to": to_date,
            "token": api_key,
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        news_items = []
        for item in data[:max_items]:
            published_at = None
            if item.get("datetime"):
                try:
                    published_at = datetime.fromtimestamp(item["datetime"])
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
        
        return news_items
    except Exception as e:
        typer.echo(f"Finnhub news API error for {ticker}: {e}")
        return []


def fetch_news_fmp(
    ticker: str, api_key: str, max_items: int = 10, as_of_date: Optional[date] = None
) -> list[NewsItem]:
    """Fetch news from FMP Stock News API (ticker-specific).
    
    Args:
        ticker: Stock ticker
        api_key: FMP API key
        max_items: Maximum number of news items to return (default: 10)
        as_of_date: If provided, fetch news up to this date (for backtesting)
    
    Returns:
        List of NewsItem objects
    """
    try:
        url = "https://financialmodelingprep.com/stable/news/stock"
        
        # Calculate date range (last 30 days or up to as_of_date)
        effective_date = as_of_date if as_of_date else date.today()
        to_date = effective_date.strftime("%Y-%m-%d")
        from_date = (effective_date - timedelta(days=30)).strftime("%Y-%m-%d")
        
        params = {
            "symbols": ticker,
            "apikey": api_key,
            "from": from_date,
            "to": to_date,
            "page": 0,
            "limit": min(max_items, 250),  # FMP limit is 250 per request
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data or not isinstance(data, list):
            return []
        
        news_items = []
        for item in data[:max_items]:  # Limit to max_items
            published_at = None
            if item.get("publishedDate"):
                try:
                    # Format: "2025-02-03 21:05:14"
                    published_at = datetime.strptime(item["publishedDate"], "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    pass
            
            # Filter by date if in backtest mode
            if as_of_date and published_at and published_at.date() > as_of_date:
                continue
            
            news_items.append(
                NewsItem(
                    ticker=ticker,
                    headline=item.get("title", ""),
                    summary=item.get("text"),
                    source=item.get("publisher", "Unknown"),
                    url=item.get("url"),
                    published_at=published_at,
                )
            )
        
        return news_items
    except Exception as e:
        typer.echo(f"FMP Stock News API error for {ticker}: {e}")
        return []


def fetch_general_news_fmp(
    api_key: str, 
    max_items: int = 50, 
    as_of_date: Optional[date] = None
) -> list[dict]:
    """Fetch general market news from FMP General News API.
    
    Args:
        api_key: FMP API key
        max_items: Maximum number of news items to return
        as_of_date: If provided, fetch news up to this date
    
    Returns:
        List of news item dicts with title, text, publishedDate, publisher, url
    """
    try:
        url = "https://financialmodelingprep.com/stable/news/general-latest"
        
        effective_date = as_of_date if as_of_date else date.today()
        to_date = effective_date.strftime("%Y-%m-%d")
        from_date = (effective_date - timedelta(days=7)).strftime("%Y-%m-%d")  # Last 7 days
        
        params = {
            "apikey": api_key,
            "from": from_date,
            "to": to_date,
            "page": 0,
            "limit": min(max_items, 250),
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data or not isinstance(data, list):
            return []
        
        news_items = []
        for item in data[:max_items]:
            published_at = None
            if item.get("publishedDate"):
                try:
                    published_at = datetime.strptime(item["publishedDate"], "%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    pass
            
            # Filter by date if in backtest mode
            if as_of_date and published_at and published_at.date() > as_of_date:
                continue
            
            news_items.append({
                "title": item.get("title", ""),
                "text": item.get("text", ""),
                "publisher": item.get("publisher", "Unknown"),
                "url": item.get("url"),
                "publishedDate": item.get("publishedDate"),
            })
        
        return news_items
    except Exception as e:
        typer.echo(f"FMP General News API error: {e}")
        return []


def fetch_news_alpha_vantage(
    ticker: str, api_key: str, max_items: int = 10, as_of_date: Optional[date] = None
) -> list[NewsItem]:
    """Fetch news and sentiment from Alpha Vantage API.
    
    Args:
        ticker: Stock ticker
        api_key: Alpha Vantage API key
        max_items: Maximum number of news items to return
        as_of_date: If provided, filter news by this date (for backtesting)
    """
    try:
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "apikey": api_key,
            "limit": min(max_items, 50),  # Alpha Vantage limit
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "feed" not in data:
            return []
        
        news_items = []
        for item in data["feed"][:max_items]:
            published_at = None
            if item.get("time_published"):
                try:
                    # Format: YYYYMMDDTHHMMSS
                    time_str = item["time_published"]
                    published_at = datetime.strptime(time_str, "%Y%m%dT%H%M%S")
                except:
                    pass
            
            # Filter by date if in backtest mode
            if as_of_date and published_at and published_at.date() > as_of_date:
                continue
            
            # Get sentiment from ticker_sentiment
            sentiment = None
            for ticker_sentiment in item.get("ticker_sentiment", []):
                if ticker_sentiment.get("ticker") == ticker:
                    sentiment_score = ticker_sentiment.get("relevance_score", 0)
                    if sentiment_score > 0.5:  # Only if relevant
                        label = ticker_sentiment.get("ticker_sentiment_label", "").lower()
                        if "bullish" in label:
                            sentiment = "bullish"
                        elif "bearish" in label:
                            sentiment = "bearish"
                        else:
                            sentiment = "neutral"
                    break
            
            news_items.append(
                NewsItem(
                    ticker=ticker,
                    headline=item.get("title", ""),
                    summary=item.get("summary"),
                    source=item.get("source", "Unknown"),
                    url=item.get("url"),
                    published_at=published_at,
                    sentiment=sentiment,
                )
            )
        
        return news_items
    except Exception as e:
        typer.echo(f"Alpha Vantage API error for {ticker}: {e}")
        return []


def fetch_historical_daily_series(
    ticker: str,
    from_date: date,
    to_date: date,
    fmp_api_key: str,
) -> list[Tuple[date, float]]:
    """Fetch daily close prices for a ticker over a date range using FMP API.

    Returns:
        List of (date, close_price) sorted by date ascending. Dates are trading days only.
    """
    if not fmp_api_key or from_date > to_date:
        return []
    try:
        url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
        params = {
            "apikey": fmp_api_key,
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
        }
        resp = requests.get(url, params=params, timeout=(5, 30))
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, dict):
            return []
        historical = data.get("historical", [])
        if not historical:
            return []
        out = []
        for day in historical:
            day_str = day.get("date")
            close = day.get("close")
            if not day_str or close is None:
                continue
            try:
                d = datetime.strptime(day_str.split()[0], "%Y-%m-%d").date()
                if from_date <= d <= to_date:
                    out.append((d, float(close)))
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda x: x[0])
        return out
    except Exception:
        return []


def fetch_price_data_finnhub(ticker: str, api_key: str) -> Optional[PriceData]:
    """Fetch price and volume data from Finnhub API."""
    try:
        url = "https://finnhub.io/api/v1/quote"
        params = {"symbol": ticker, "token": api_key}
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data or data.get("c") is None:  # 'c' is current price
            return None
        
        # Get additional info from profile endpoint for market cap
        market_cap = None
        try:
            profile_url = "https://finnhub.io/api/v1/stock/profile2"
            profile_response = requests.get(
                profile_url, params={"symbol": ticker, "token": api_key}, timeout=(5, 30)
            )
            if profile_response.status_code == 200:
                profile_data = profile_response.json()
                market_cap = profile_data.get("marketCapitalization")
        except Exception:
            pass  # Market cap is optional
        
        # Calculate price change percentage
        price = data.get("c", 0)  # Current price
        prev_close = data.get("pc", price)  # Previous close
        price_change_pct = None
        if prev_close and prev_close != 0:
            price_change_pct = ((price - prev_close) / prev_close) * 100
        
        # Volume data
        volume = data.get("v", 0)  # Current volume
        
        return PriceData(
            ticker=ticker,
            price=float(price),
            volume=int(volume) if volume else 0,
            avg_volume_30d=None,  # Would need historical data
            market_cap=float(market_cap) if market_cap else None,
            price_change_pct=price_change_pct,
            price_change_pct_5d=None,
            price_change_pct_20d=None,
            as_of=datetime.now(),
        )
    except Exception as e:
        typer.echo(f"Finnhub quote API error for {ticker}: {e}")
        return None


def fetch_price_data_fmp(ticker: str, api_key: str) -> Optional[PriceData]:
    """Fetch price, volume, market cap from FMP quote API.
    Also calculates avg_volume_30d from historical data."""
    try:
        url = "https://financialmodelingprep.com/api/v3/quote/{}".format(ticker)
        params = {"apikey": api_key}
        resp = requests.get(url, params=params, timeout=(5, 30))
        resp.raise_for_status()
        data = resp.json()
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        q = data[0]
        price = q.get("price")
        volume = q.get("volume")
        market_cap = q.get("marketCap")
        prev_close = q.get("previousClose")
        price_change_pct = None
        if price is not None and prev_close:
            try:
                price_change_pct = ((float(price) - float(prev_close)) / float(prev_close)) * 100
            except Exception:
                price_change_pct = None
        
        # Fetch profile for beta
        beta = None
        try:
            profile_url = "https://financialmodelingprep.com/stable/profile"
            profile_params = {"symbol": ticker, "apikey": api_key}
            profile_resp = requests.get(profile_url, params=profile_params, timeout=(5, 30))
            if profile_resp.status_code == 200:
                profile_data = profile_resp.json()
                if isinstance(profile_data, list) and profile_data and isinstance(profile_data[0], dict):
                    beta = profile_data[0].get("beta")
        except Exception:
            beta = None
        
        # Technical indicators (SMA, RSI)
        sma_20 = None
        sma_50 = None
        rsi_14 = None
        try:
            ti_base = "https://financialmodelingprep.com/stable/technical-indicators"
            for period, attr in [(20, "sma_20"), (50, "sma_50")]:
                params = {
                    "symbol": ticker,
                    "periodLength": period,
                    "timeframe": "1day",
                    "apikey": api_key,
                }
                resp_ti = requests.get(f"{ti_base}/sma", params=params, timeout=(5, 30))
                if resp_ti.status_code == 200:
                    ti_data = resp_ti.json()
                    if isinstance(ti_data, list) and ti_data and isinstance(ti_data[0], dict):
                        sma_val = ti_data[0].get("sma")
                        if sma_val is not None:
                            if attr == "sma_20":
                                sma_20 = float(sma_val)
                            else:
                                sma_50 = float(sma_val)
            # RSI
            params_rsi = {
                "symbol": ticker,
                "periodLength": 14,
                "timeframe": "1day",
                "apikey": api_key,
            }
            resp_rsi = requests.get(f"{ti_base}/rsi", params=params_rsi, timeout=(5, 30))
            if resp_rsi.status_code == 200:
                rsi_data = resp_rsi.json()
                if isinstance(rsi_data, list) and rsi_data and isinstance(rsi_data[0], dict):
                    rsi_val = rsi_data[0].get("rsi")
                    if rsi_val is not None:
                        rsi_14 = float(rsi_val)
        except Exception:
            pass
        
        # Calculate avg_volume_30d from historical data
        avg_volume_30d = None
        price_change_pct_5d = None
        price_change_pct_20d = None
        try:
            to_date = date.today()
            from_date = to_date - timedelta(days=30)
            hist_url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
            hist_params = {"apikey": api_key, "from": from_date.strftime("%Y-%m-%d"), "to": to_date.strftime("%Y-%m-%d")}
            hist_resp = requests.get(hist_url, params=hist_params, timeout=(5, 30))
            if hist_resp.status_code == 200:
                hist_data = hist_resp.json()
                if isinstance(hist_data, dict) and "historical" in hist_data:
                    volumes = [day.get("volume", 0) for day in hist_data["historical"] if day.get("volume")]
                    if volumes:
                        avg_volume_30d = int(sum(volumes) / len(volumes))
                    
                    closes = [day.get("close") for day in hist_data["historical"] if day.get("close") is not None]
                    if closes:
                        try:
                            latest_close = float(closes[0])
                            if len(closes) > 5:
                                prev_5 = float(closes[5])
                                price_change_pct_5d = ((latest_close - prev_5) / prev_5) * 100 if prev_5 != 0 else None
                            if len(closes) > 20:
                                prev_20 = float(closes[20])
                                price_change_pct_20d = ((latest_close - prev_20) / prev_20) * 100 if prev_20 != 0 else None
                        except Exception:
                            pass
        except Exception:
            pass  # avg_volume_30d is optional
        
        return PriceData(
            ticker=ticker,
            price=float(price) if price is not None else None,
            volume=int(volume) if volume else 0,
            avg_volume_30d=avg_volume_30d,
            market_cap=float(market_cap) if market_cap else None,
            price_change_pct=price_change_pct,
            price_change_pct_5d=price_change_pct_5d,
            price_change_pct_20d=price_change_pct_20d,
            beta=beta,
            sma_20=sma_20,
            sma_50=sma_50,
            rsi_14=rsi_14,
            as_of=datetime.now(),
        )
    except Exception:
        return None

def fetch_fundamentals_fmp(ticker: str, api_key: str, as_of_date: Optional[date] = None) -> Optional[Fundamentals]:
    """Fetch fundamental data from Financial Modeling Prep API.
    
    Rate limit: 300 calls/min = 5 calls/sec = 200ms per call minimum.
    We use 250ms between calls to stay safely under the limit.
    
    Note: Some tickers use dots (BRK.B) which FMP may require as dashes (BRK-B).
    """
    # Handle ticker notation: FMP may use BRK-B instead of BRK.B
    fmp_ticker = ticker.replace(".", "-") if "." in ticker else ticker
    
    # FMP rate limit: 300 calls/min = 200ms per call minimum
    # Use 250ms to be safe
    FMP_RATE_LIMIT_DELAY = 0.25  # seconds
    
    try:
        # For historical backtesting, get more statements to find the right one
        limit = 10 if as_of_date else 2
        income_url = f"https://financialmodelingprep.com/api/v3/income-statement/{fmp_ticker}"
        income_params = {"apikey": api_key, "limit": limit}
        
        income_start = time.time()
        # Use tuple timeout: (connect_timeout, read_timeout) to handle sleep/wake scenarios
        income_response = requests.get(income_url, params=income_params, timeout=(5, 30))
        income_elapsed = time.time() - income_start
        if income_elapsed > 2.0:  # Log if slow
            typer.echo(f"  [SLOW] Income statement API call took {income_elapsed:.2f}s for {ticker}")
        income_response.raise_for_status()
        income_data = income_response.json()
        
        # Check for API errors
        if isinstance(income_data, dict) and "Error Message" in income_data:
            typer.echo(f"  [WARN] FMP API error for {ticker}: {income_data.get('Error Message')}")
            return None
        
        if not income_data or not isinstance(income_data, list) or len(income_data) == 0:
            typer.echo(f"  [WARN] No income statement data available for {ticker}")
            return None
        
        # For backtesting, filter to find the most recent statement before as_of_date
        if as_of_date:
            # Income statements have a "date" field (YYYY-MM-DD format)
            # Find the most recent statement with date <= as_of_date
            valid_statements = []
            for stmt in income_data:
                stmt_date_str = stmt.get("date")
                if stmt_date_str:
                    try:
                        stmt_date = datetime.strptime(stmt_date_str, "%Y-%m-%d").date()
                        if stmt_date <= as_of_date:
                            valid_statements.append((stmt_date, stmt))
                    except (ValueError, TypeError):
                        continue
            
            if not valid_statements:
                typer.echo(f"  [WARN] No income statements found before {as_of_date} for {ticker}")
                return None
            
            # Sort by date descending and take the most recent
            valid_statements.sort(key=lambda x: x[0], reverse=True)
            latest_income = valid_statements[0][1]
            
            # Get previous period for YoY growth (next most recent before as_of_date)
            prev_income = valid_statements[1][1] if len(valid_statements) > 1 else None
        else:
            # Normal mode: use most recent
            latest_income = income_data[0]
            prev_income = income_data[1] if len(income_data) > 1 else None
        
        # Rate limit: wait before next API call
        time.sleep(FMP_RATE_LIMIT_DELAY)
        
        # Get financial ratios
        ratios_url = f"https://financialmodelingprep.com/api/v3/ratios/{fmp_ticker}"
        ratios_params = {"apikey": api_key, "limit": 1}
        
        ratios_start = time.time()
        ratios_response = requests.get(ratios_url, params=ratios_params, timeout=(5, 30))
        ratios_elapsed = time.time() - ratios_start
        if ratios_elapsed > 2.0:  # Log if slow
            typer.echo(f"  [SLOW] Ratios API call took {ratios_elapsed:.2f}s for {ticker}")
        ratios_response.raise_for_status()
        ratios_data = None
        if ratios_response.status_code == 200:
            ratios_json = ratios_response.json()
            # /api/v3/ratios/ returns a list
            if ratios_json and isinstance(ratios_json, list) and len(ratios_json) > 0:
                ratios_data = ratios_json[0]
            elif ratios_json and isinstance(ratios_json, dict):
                ratios_data = ratios_json
        
        # Extract revenue
        revenue_ttm = latest_income.get("revenue")
        
        # Calculate revenue YoY growth (primary: financial-growth endpoint; fallback: income statements)
        revenue_yoy_growth = None

        # Primary: financial-growth endpoint (fewer calls overall vs multi-statement math)
        try:
            fg_url = "https://financialmodelingprep.com/stable/financial-growth"
            fg_params = {"symbol": fmp_ticker, "apikey": api_key, "limit": 1}
            fg_start = time.time()
            fg_resp = requests.get(fg_url, params=fg_params, timeout=(5, 30))
            fg_elapsed = time.time() - fg_start
            if fg_elapsed > 2.0:  # Log if slow
                typer.echo(f"  [SLOW] Financial growth API call took {fg_elapsed:.2f}s for {ticker}")
            if fg_resp.status_code == 200:
                fg_json = fg_resp.json()
                fg_data = None
                if isinstance(fg_json, list) and len(fg_json) > 0:
                    fg_data = fg_json[0]
                elif isinstance(fg_json, dict):
                    fg_data = fg_json
                if fg_data:
                    rev_growth = fg_data.get("revenueGrowth")
                    if rev_growth is not None:
                        # API returns decimal (e.g., 0.02 = 2%)
                        revenue_yoy_growth = rev_growth * 100
            # Rate limit between calls
            time.sleep(FMP_RATE_LIMIT_DELAY)
        except Exception:
            pass  # Optional

        # Fallback: compute from income statements if growth endpoint missing
        if revenue_yoy_growth is None and prev_income:
            prev_revenue = prev_income.get("revenue")
            if prev_revenue and prev_revenue != 0:
                revenue_yoy_growth = ((revenue_ttm - prev_revenue) / prev_revenue) * 100
        
        # Get operating margin
        operating_income = latest_income.get("operatingIncome")
        operating_margin = None
        if revenue_ttm and operating_income and revenue_ttm != 0:
            operating_margin = (operating_income / revenue_ttm) * 100
        
        # Rate limit: wait before next API call
        time.sleep(FMP_RATE_LIMIT_DELAY)
        
        # Get FCF margin (from cash flow statement)
        # Match to the same period as income statement
        fcf_margin = None
        try:
            cf_limit = 10 if as_of_date else 1
            cf_url = f"https://financialmodelingprep.com/api/v3/cash-flow-statement/{fmp_ticker}"
            cf_params = {"apikey": api_key, "limit": cf_limit}
            cf_start = time.time()
            cf_response = requests.get(cf_url, params=cf_params, timeout=(5, 30))
            cf_elapsed = time.time() - cf_start
            if cf_elapsed > 2.0:  # Log if slow
                typer.echo(f"  [SLOW] Cash flow API call took {cf_elapsed:.2f}s for {ticker}")
            if cf_response.status_code == 200:
                cf_data = cf_response.json()
                if cf_data and isinstance(cf_data, list) and len(cf_data) > 0:
                    # For backtesting, find the statement matching the income statement date
                    if as_of_date:
                        # Get the date from the income statement we selected
                        income_date_str = latest_income.get("date")
                        cash_flow_stmt = None
                        
                        if income_date_str:
                            try:
                                income_date = datetime.strptime(income_date_str, "%Y-%m-%d").date()
                                # Find cash flow statement with matching date
                                for cf in cf_data:
                                    cf_date_str = cf.get("date")
                                    if cf_date_str:
                                        try:
                                            cf_date = datetime.strptime(cf_date_str, "%Y-%m-%d").date()
                                            if cf_date == income_date:
                                                cash_flow_stmt = cf
                                                break
                                        except (ValueError, TypeError):
                                            continue
                                
                                # If no exact match, find most recent before as_of_date
                                if not cash_flow_stmt:
                                    valid_cf = []
                                    for cf in cf_data:
                                        cf_date_str = cf.get("date")
                                        if cf_date_str:
                                            try:
                                                cf_date = datetime.strptime(cf_date_str, "%Y-%m-%d").date()
                                                if cf_date <= as_of_date:
                                                    valid_cf.append((cf_date, cf))
                                            except (ValueError, TypeError):
                                                continue
                                    if valid_cf:
                                        valid_cf.sort(key=lambda x: x[0], reverse=True)
                                        cash_flow_stmt = valid_cf[0][1]
                            except (ValueError, TypeError):
                                pass
                        
                        if not cash_flow_stmt:
                            # Fallback to most recent if no match found
                            cash_flow_stmt = cf_data[0]
                    else:
                        cash_flow_stmt = cf_data[0]
                    
                    free_cash_flow = cash_flow_stmt.get("freeCashFlow")
                    if free_cash_flow is not None and revenue_ttm and revenue_ttm != 0:
                        fcf_margin = (free_cash_flow / revenue_ttm) * 100
        except Exception:
            pass  # FCF is optional
        
        # Get ratios from ratios endpoint
        pe_ratio = ratios_data.get("priceEarningsRatio") if ratios_data else None
        # Try ratios endpoint first, but it often returns 0 (invalid)
        ev_ebitda_from_ratios = ratios_data.get("enterpriseValueMultiple") if ratios_data else None
        # Treat 0 as invalid (data quality issue from FMP)
        if ev_ebitda_from_ratios == 0:
            ev_ebitda_from_ratios = None
        
        # Rate limit: wait before next API call
        time.sleep(FMP_RATE_LIMIT_DELAY)
        
        # Get ROIC, net_debt_to_ebitda, and EV/EBITDA from key-metrics-ttm endpoint
        roic = None
        net_debt_to_ebitda = None
        ev_ebitda_from_metrics = None
        try:
            km_url = f"https://financialmodelingprep.com/api/v3/key-metrics-ttm/{fmp_ticker}"
            km_params = {"apikey": api_key}
            km_start = time.time()
            km_resp = requests.get(km_url, params=km_params, timeout=(5, 30))
            km_elapsed = time.time() - km_start
            if km_elapsed > 2.0:  # Log if slow
                typer.echo(f"  [SLOW] Key metrics API call took {km_elapsed:.2f}s for {ticker}")
            if km_resp.status_code == 200:
                km_json = km_resp.json()
                if isinstance(km_json, list) and len(km_json) > 0:
                    km_data = km_json[0]
                    # roicTTM is already a decimal (0.5569 = 55.69%), convert to percentage
                    roic_ttm = km_data.get("roicTTM")
                    if roic_ttm is not None:
                        roic = roic_ttm * 100
                    # netDebtToEBITDATTM is already a ratio
                    net_debt_to_ebitda = km_data.get("netDebtToEBITDATTM")
                    # evToEBITDA from key-metrics (preferred source)
                    ev_ebitda_from_metrics = km_data.get("evToEBITDA")
                    if ev_ebitda_from_metrics == 0:
                        ev_ebitda_from_metrics = None
                elif isinstance(km_json, dict):
                    roic_ttm = km_json.get("roicTTM")
                    if roic_ttm is not None:
                        roic = roic_ttm * 100
                    net_debt_to_ebitda = km_json.get("netDebtToEBITDATTM")
                    ev_ebitda_from_metrics = km_json.get("evToEBITDA")
                    if ev_ebitda_from_metrics == 0:
                        ev_ebitda_from_metrics = None
        except Exception:
            pass  # These are optional metrics
        
        # Try to calculate EV/EBITDA if not available from metrics
        ev_ebitda = ev_ebitda_from_metrics or ev_ebitda_from_ratios
        if ev_ebitda is None or ev_ebitda == 0:
            # Fallback: calculate from enterprise value and EBITDA
            try:
                # Rate limit: wait before next API call
                time.sleep(FMP_RATE_LIMIT_DELAY)
                
                # Get enterprise value
                ev_url = f"https://financialmodelingprep.com/stable/enterprise-values"
                ev_params = {"symbol": fmp_ticker, "apikey": api_key, "limit": 1}
                ev_resp = requests.get(ev_url, params=ev_params, timeout=(5, 30))
                enterprise_value = None
                if ev_resp.status_code == 200:
                    ev_json = ev_resp.json()
                    if isinstance(ev_json, list) and len(ev_json) > 0:
                        ev_data = ev_json[0]
                        enterprise_value = ev_data.get("enterpriseValue")
                    elif isinstance(ev_json, dict):
                        enterprise_value = ev_json.get("enterpriseValue")
                
                # Get EBITDA from income statement (already fetched)
                ebitda = latest_income.get("ebitda")
                
                # Calculate EV/EBITDA
                if enterprise_value and ebitda and ebitda != 0 and enterprise_value > 0:
                    ev_ebitda = enterprise_value / ebitda
                else:
                    # Calculation failed (missing data or invalid values) - set to penalty value and warn
                    ev_ebitda = 30.0  # High value = penalty in scoring
                    reason = []
                    if enterprise_value is None:
                        reason.append("enterprise_value missing")
                    elif enterprise_value <= 0:
                        reason.append(f"enterprise_value invalid ({enterprise_value})")
                    if ebitda is None:
                        reason.append("EBITDA missing")
                    elif ebitda == 0:
                        reason.append("EBITDA is zero")
                    typer.echo(f"  [WARN] Could not calculate EV/EBITDA for {ticker} ({', '.join(reason)}), setting to penalty value (30.0)")
            except Exception as e:
                # Calculation failed - set to penalty value
                ev_ebitda = 30.0
                typer.echo(f"  [WARN] Error calculating EV/EBITDA for {ticker}: {e}, setting to penalty value (30.0)")
        
        # Use as_of_date if provided (for backtesting), otherwise use today
        effective_as_of = as_of_date if as_of_date else date.today()
        
        return Fundamentals(
            ticker=ticker,
            revenue_ttm=float(revenue_ttm) if revenue_ttm else None,
            revenue_yoy_growth=revenue_yoy_growth,
            operating_margin_ttm=operating_margin,
            fcf_margin_ttm=fcf_margin,
            roic=roic,
            net_debt_to_ebitda=net_debt_to_ebitda,
            pe_ratio=float(pe_ratio) if pe_ratio else None,
            ev_ebitda=float(ev_ebitda) if ev_ebitda is not None else None,
            as_of=effective_as_of,
        )
    except requests.exceptions.Timeout as e:
        typer.echo(f"  [ERROR] FMP API timeout for {ticker} (request took too long)")
        typer.echo(f"  This may happen if the computer went to sleep. Consider resuming the fetch.")
        return None
    except requests.exceptions.ConnectionError as e:
        typer.echo(f"  [ERROR] FMP API connection error for {ticker} (network issue)")
        typer.echo(f"  This may happen if the computer went to sleep. Consider resuming the fetch.")
        return None
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            typer.echo(f"  [ERROR] FMP API authentication error for {ticker} (check API key)")
        elif e.response.status_code == 429:
            typer.echo(f"  [ERROR] FMP API rate limit exceeded for {ticker}")
        else:
            typer.echo(f"  [ERROR] FMP API HTTP error for {ticker}: {e}")
        return None
    except Exception as e:
        typer.echo(f"  [ERROR] FMP API error for {ticker}: {e}")
        return None

