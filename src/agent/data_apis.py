from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
import typer

from .models import AnalystRecommendation, NewsItem, PriceData, Fundamentals


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
        
        # Get price targets from another endpoint
        price_target = None
        price_target_high = None
        price_target_low = None
        
        try:
            targets_url = "https://finnhub.io/api/v1/stock/price-target"
            targets_response = requests.get(targets_url, params={"symbol": ticker, "token": api_key}, timeout=10)
            targets_response.raise_for_status()
            targets_data = targets_response.json()
            if targets_data and isinstance(targets_data, dict):
                price_target = targets_data.get("targetMean")
                price_target_high = targets_data.get("targetHigh")
                price_target_low = targets_data.get("targetLow")
        except Exception as e:
            # Price targets are optional, so just log and continue
            typer.echo(f"  [WARN] Could not fetch price targets for {ticker}: {e}")
        
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
            as_of=date.today(),
        )
    except Exception as e:
        typer.echo(f"Finnhub API error for {ticker}: {e}")
        return None


def fetch_news_finnhub(
    ticker: str, api_key: str, max_items: int = 10
) -> list[NewsItem]:
    """Fetch news from Finnhub API."""
    try:
        # Get company news from past 30 days
        url = "https://finnhub.io/api/v1/company-news"
        to_date = date.today().strftime("%Y-%m-%d")
        from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        
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


def fetch_news_alpha_vantage(
    ticker: str, api_key: str, max_items: int = 10
) -> list[NewsItem]:
    """Fetch news and sentiment from Alpha Vantage API."""
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
                profile_url, params={"symbol": ticker, "token": api_key}, timeout=10
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
            as_of=datetime.now(),
        )
    except Exception as e:
        typer.echo(f"Finnhub quote API error for {ticker}: {e}")
        return None


def fetch_fundamentals_fmp(ticker: str, api_key: str) -> Optional[Fundamentals]:
    """Fetch fundamental data from Financial Modeling Prep API.
    
    Rate limit: 300 calls/min = 5 calls/sec = 200ms per call minimum.
    We use 250ms between calls to stay safely under the limit.
    """
    # FMP rate limit: 300 calls/min = 200ms per call minimum
    # Use 250ms to be safe
    FMP_RATE_LIMIT_DELAY = 0.25  # seconds
    
    try:
        # Get income statement (most recent)
        income_url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}"
        income_params = {"apikey": api_key, "limit": 2}  # Get 2 periods for YoY growth
        
        income_response = requests.get(income_url, params=income_params, timeout=10)
        income_response.raise_for_status()
        income_data = income_response.json()
        
        # Check for API errors
        if isinstance(income_data, dict) and "Error Message" in income_data:
            typer.echo(f"  [WARN] FMP API error for {ticker}: {income_data.get('Error Message')}")
            return None
        
        if not income_data or not isinstance(income_data, list) or len(income_data) == 0:
            typer.echo(f"  [WARN] No income statement data available for {ticker}")
            return None
        
        latest_income = income_data[0]
        
        # Get previous period for YoY growth
        prev_income = income_data[1] if len(income_data) > 1 else None
        
        # Rate limit: wait before next API call
        time.sleep(FMP_RATE_LIMIT_DELAY)
        
        # Get financial ratios
        ratios_url = f"https://financialmodelingprep.com/api/v3/ratios/{ticker}"
        ratios_params = {"apikey": api_key, "limit": 1}
        
        ratios_response = requests.get(ratios_url, params=ratios_params, timeout=10)
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
        
        # Calculate revenue YoY growth
        revenue_yoy_growth = None
        if prev_income:
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
        fcf_margin = None
        try:
            cf_url = f"https://financialmodelingprep.com/api/v3/cash-flow-statement/{ticker}"
            cf_params = {"apikey": api_key, "limit": 1}
            cf_response = requests.get(cf_url, params=cf_params, timeout=10)
            if cf_response.status_code == 200:
                cf_data = cf_response.json()
                if cf_data and isinstance(cf_data, list) and len(cf_data) > 0:
                    free_cash_flow = cf_data[0].get("freeCashFlow")
                    if free_cash_flow and revenue_ttm and revenue_ttm != 0:
                        fcf_margin = (free_cash_flow / revenue_ttm) * 100
        except Exception:
            pass  # FCF is optional
        
        # Get ratios from ratios endpoint
        pe_ratio = ratios_data.get("priceEarningsRatio") if ratios_data else None
        ev_ebitda = ratios_data.get("enterpriseValueMultiple") if ratios_data else None
        roic = ratios_data.get("returnOnInvestedCapital") if ratios_data else None
        if roic:
            roic = roic * 100  # Convert to percentage
        
        # Get net debt to EBITDA
        net_debt_to_ebitda = None
        if ratios_data:
            net_debt_to_ebitda = ratios_data.get("netDebtToEBITDA")
        
        return Fundamentals(
            ticker=ticker,
            revenue_ttm=float(revenue_ttm) if revenue_ttm else None,
            revenue_yoy_growth=revenue_yoy_growth,
            operating_margin_ttm=operating_margin,
            fcf_margin_ttm=fcf_margin,
            roic=roic,
            net_debt_to_ebitda=net_debt_to_ebitda,
            pe_ratio=float(pe_ratio) if pe_ratio else None,
            ev_ebitda=float(ev_ebitda) if ev_ebitda else None,
            as_of=date.today(),
        )
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

