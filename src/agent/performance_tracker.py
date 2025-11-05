from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import typer

from .config import load_config
from .data_apis import fetch_price_data_finnhub, fetch_price_data_fmp
from .data_fetcher import fetch_price_data
from .models import Portfolio, PortfolioHolding

app = typer.Typer()


def fetch_historical_price(ticker: str, target_date: date, fmp_api_key: Optional[str]) -> Optional[float]:
    """Fetch historical price for a specific date using FMP API."""
    if not fmp_api_key:
        return None
    
    try:
        # FMP historical data endpoint
        hist_url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
        # Get a range around the target date to ensure we have data
        from_date = target_date - timedelta(days=5)
        to_date = target_date + timedelta(days=1)
        
        hist_params = {
            "apikey": fmp_api_key,
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
        }
        
        hist_resp = requests.get(hist_url, params=hist_params, timeout=10)
        if hist_resp.status_code != 200:
            return None
        
        hist_data = hist_resp.json()
        if not hist_data or not isinstance(hist_data, dict):
            return None
        
        historical = hist_data.get("historical", [])
        if not historical:
            return None
        
        # Historical data is typically sorted newest first
        # Find the closest date to target_date
        best_match = None
        min_diff = timedelta(days=365)
        
        for day_data in historical:
            day_str = day_data.get("date")
            if not day_str:
                continue
            
            try:
                day_date = datetime.strptime(day_str.split()[0], "%Y-%m-%d").date()
                diff = abs(day_date - target_date)
                if diff < min_diff:
                    min_diff = diff
                    best_match = day_data
            except Exception:
                continue
        
        if best_match:
            return best_match.get("close")
        
        return None
    
    except Exception as e:
        typer.echo(f"  [WARN] Error fetching historical price for {ticker}: {e}")
        return None


def track_performance(
    portfolio_file: Path,
    out: Optional[Path] = None,
    use_stored_prices: bool = False,
) -> None:
    """Track portfolio performance since construction."""
    cfg = load_config()
    
    # Load portfolio
    if not portfolio_file.exists():
        typer.echo(f"Portfolio file not found: {portfolio_file}")
        raise typer.Exit(code=1)
    
    portfolio_data = json.loads(portfolio_file.read_text(encoding='utf-8'))
    try:
        portfolio = Portfolio.model_validate(portfolio_data)
    except Exception as e:
        typer.echo(f"Failed to parse portfolio file: {e}")
        raise typer.Exit(code=1)
    
    # Parse construction date
    constructed_at = portfolio.constructed_at
    if isinstance(constructed_at, str):
        constructed_at = datetime.fromisoformat(constructed_at.replace('Z', '+00:00'))
    
    construction_date = constructed_at.date()
    days_held = (date.today() - construction_date).days
    
    typer.echo(f"Portfolio Performance Tracking")
    typer.echo(f"Construction Date: {construction_date.strftime('%Y-%m-%d')}")
    typer.echo(f"Days Held: {days_held}")
    typer.echo(f"Tracking {len(portfolio.holdings)} holdings...")
    typer.echo("")
    
    # Fetch prices
    performance_data = []
    
    for i, holding in enumerate(portfolio.holdings):
        typer.echo(f"[{i+1}/{len(portfolio.holdings)}] {holding.ticker}...", nl=False)
        
        # Get historical price (construction date)
        if use_stored_prices and hasattr(holding, 'price') and holding.price:
            # Try to use stored price if available (would need to add to PortfolioHolding model)
            construction_price = None
        else:
            construction_price = fetch_historical_price(
                holding.ticker, construction_date, cfg.fmp_api_key
            )
            time.sleep(0.25)  # Rate limit
        
        # Get current price
        current_price_data = fetch_price_data(
            holding.ticker, cfg.finnhub_api_key, cfg.fmp_api_key
        )
        current_price = current_price_data.price if current_price_data else None
        
        if construction_price and current_price:
            return_pct = ((current_price - construction_price) / construction_price) * 100
            contribution = holding.weight * return_pct
            performance_data.append({
                "ticker": holding.ticker,
                "weight": holding.weight,
                "construction_price": construction_price,
                "current_price": current_price,
                "return_pct": return_pct,
                "contribution": contribution,
                "sector": holding.sector,
                "theme": holding.theme,
            })
            typer.echo(f" {return_pct:+.2f}% (${construction_price:.2f} -> ${current_price:.2f})")
        else:
            performance_data.append({
                "ticker": holding.ticker,
                "weight": holding.weight,
                "construction_price": construction_price,
                "current_price": current_price,
                "return_pct": None,
                "contribution": None,
                "sector": holding.sector,
                "theme": holding.theme,
            })
            typer.echo(" [N/A - missing price data]")
    
    # Calculate portfolio metrics
    valid_performances = [p for p in performance_data if p["return_pct"] is not None]
    
    if len(valid_performances) == 0:
        typer.echo("[ERROR] No valid performance data calculated")
        raise typer.Exit(code=1)
    
    # Portfolio return (weighted)
    portfolio_return = sum(p["contribution"] for p in valid_performances)
    
    # Simple average return
    avg_return = sum(p["return_pct"] for p in valid_performances) / len(valid_performances)
    
    # Distribution
    returns_list = [p["return_pct"] for p in valid_performances]
    returns_list_sorted = sorted(returns_list)
    median_return = returns_list_sorted[len(returns_list_sorted) // 2]
    
    # Winners and losers
    winners = [p for p in valid_performances if p["return_pct"] > 0]
    losers = [p for p in valid_performances if p["return_pct"] < 0]
    
    # Sector performance
    sector_returns = defaultdict(list)
    sector_contributions = defaultdict(float)
    for p in valid_performances:
        sector = p["sector"] or "Unknown"
        sector_returns[sector].append(p["return_pct"])
        sector_contributions[sector] += p["contribution"]
    
    # Print report
    typer.echo("")
    typer.echo("=" * 80)
    typer.echo("PORTFOLIO PERFORMANCE REPORT")
    typer.echo("=" * 80)
    typer.echo("")
    typer.echo(f"Performance Period: {construction_date.strftime('%Y-%m-%d')} to {date.today().strftime('%Y-%m-%d')} ({days_held} days)")
    typer.echo("")
    typer.echo("Portfolio-Level Metrics:")
    typer.echo(f"  Weighted Portfolio Return: {portfolio_return:+.2f}%")
    typer.echo(f"  Simple Average Return: {avg_return:+.2f}%")
    typer.echo(f"  Median Return: {median_return:+.2f}%")
    typer.echo(f"  Min Return: {min(returns_list):+.2f}%")
    typer.echo(f"  Max Return: {max(returns_list):+.2f}%")
    typer.echo("")
    typer.echo(f"Winners: {len(winners)}/{len(valid_performances)} ({len(winners)/len(valid_performances)*100:.1f}%)")
    typer.echo(f"Losers: {len(losers)}/{len(valid_performances)} ({len(losers)/len(valid_performances)*100:.1f}%)")
    typer.echo("")
    
    typer.echo("Top 5 Performers:")
    sorted_by_return = sorted(valid_performances, key=lambda x: x["return_pct"], reverse=True)
    for i, p in enumerate(sorted_by_return[:5], 1):
        typer.echo(f"  {i}. {p['ticker']}: {p['return_pct']:+.2f}% (weight: {p['weight']*100:.1f}%, contribution: {p['contribution']:+.2f}%)")
    
    typer.echo("")
    typer.echo("Bottom 5 Performers:")
    for i, p in enumerate(sorted_by_return[-5:], 1):
        typer.echo(f"  {i}. {p['ticker']}: {p['return_pct']:+.2f}% (weight: {p['weight']*100:.1f}%, contribution: {p['contribution']:+.2f}%)")
    
    typer.echo("")
    typer.echo("Sector Performance:")
    for sector, returns in sorted(sector_contributions.items(), key=lambda x: x[1], reverse=True):
        avg_sector_return = sum(sector_returns[sector]) / len(sector_returns[sector])
        contribution = sector_contributions[sector]
        count = len(sector_returns[sector])
        typer.echo(f"  {sector}: {avg_sector_return:+.2f}% avg return, {contribution:+.2f}% portfolio contribution ({count} stocks)")
    
    typer.echo("")
    typer.echo("=" * 80)
    
    # Write detailed report if output specified
    if out:
        report = {
            "analysis_date": datetime.now().isoformat(),
            "portfolio_file": str(portfolio_file),
            "construction_date": construction_date.isoformat(),
            "current_date": date.today().isoformat(),
            "days_held": days_held,
            "portfolio_metrics": {
                "weighted_return": portfolio_return,
                "simple_avg_return": avg_return,
                "median_return": median_return,
                "min_return": min(returns_list),
                "max_return": max(returns_list),
                "winners_count": len(winners),
                "losers_count": len(losers),
                "total_holdings": len(valid_performances),
            },
            "holdings": performance_data,
            "sector_performance": {
                sector: {
                    "avg_return": sum(returns) / len(returns),
                    "contribution": sector_contributions[sector],
                    "count": len(returns),
                }
                for sector, returns in sector_returns.items()
            },
        }
        
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        typer.echo(f"\nDetailed report written to {out}")


@app.command()
def track(
    portfolio_file: Path = typer.Option(
        Path("data/portfolio.json"), help="Input portfolio JSON path"
    ),
    out: Optional[Path] = typer.Option(
        None, help="Output detailed report JSON path (optional)"
    ),
    use_stored_prices: bool = typer.Option(
        False, help="Use stored prices from portfolio (if available)"
    ),
):
    """Track portfolio performance since construction date."""
    track_performance(portfolio_file, out, use_stored_prices)

