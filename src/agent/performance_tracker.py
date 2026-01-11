from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import typer
import yfinance as yf

from .config import load_config
from .data_apis import fetch_price_data_finnhub, fetch_price_data_fmp
from .data_fetcher import fetch_price_data
from .models import Portfolio, PortfolioHolding
from .run_manager import find_all_portfolios

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
        
        hist_resp = requests.get(hist_url, params=hist_params, timeout=(5, 30))
        if hist_resp.status_code != 200:
            return None
        
        try:
            hist_data = hist_resp.json()
        except (ValueError, json.JSONDecodeError):
            typer.echo(f"  [WARN] Invalid JSON response for {ticker} historical price")
            return None
        
        if not hist_data or not isinstance(hist_data, dict):
            return None
        
        historical = hist_data.get("historical", [])
        if not historical:
            return None
        
        # Historical data is typically sorted newest first
        # Find the closest date on or before target_date (prefer exact match, then closest before)
        # Only use dates after target_date if no earlier date is available
        best_match = None
        best_match_after = None
        min_diff_before = timedelta(days=365)
        min_diff_after = timedelta(days=365)
        
        for day_data in historical:
            day_str = day_data.get("date")
            if not day_str:
                continue
            
            try:
                day_date = datetime.strptime(day_str.split()[0], "%Y-%m-%d").date()
                
                if day_date <= target_date:
                    # Prefer dates on or before target_date
                    diff = target_date - day_date
                    if diff < min_diff_before:
                        min_diff_before = diff
                        best_match = day_data
                else:
                    # Only consider dates after if we don't have a before match
                    diff = day_date - target_date
                    if diff < min_diff_after:
                        min_diff_after = diff
                        best_match_after = day_data
            except Exception:
                continue
        
        # Use best_match (on or before) if available, otherwise fall back to after
        if best_match is None and best_match_after is not None:
            best_match = best_match_after
            # Warn if we had to use a date after target_date
            if min_diff_after.days > 0:
                typer.echo(f"  [WARN] No historical data for {ticker} on or before {target_date}, using {target_date + min_diff_after}")
        
        if best_match:
            # Check how far the matched date is from target
            matched_date_str = best_match.get("date", "")
            if matched_date_str:
                try:
                    matched_date = datetime.strptime(matched_date_str.split()[0], "%Y-%m-%d").date()
                    date_diff = abs((matched_date - target_date).days)
                    if date_diff > 5:
                        typer.echo(f"  [WARN] Historical price for {ticker} is {date_diff} days from target date {target_date}")
                except Exception:
                    pass
            
            price = best_match.get("close")
            # Validate price is a positive number
            if price is not None:
                try:
                    price_float = float(price)
                    if price_float > 0:
                        return price_float
                except (ValueError, TypeError):
                    pass
        
        return None
    
    except requests.exceptions.Timeout:
        typer.echo(f"  [WARN] Timeout fetching historical price for {ticker}")
        return None
    except requests.exceptions.ConnectionError:
        typer.echo(f"  [WARN] Connection error fetching historical price for {ticker}")
        return None
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            typer.echo(f"  [WARN] Authentication error fetching historical price for {ticker} (check API key)")
        elif e.response.status_code == 429:
            typer.echo(f"  [WARN] Rate limit exceeded for {ticker}")
        else:
            typer.echo(f"  [WARN] HTTP error fetching historical price for {ticker}: {e.response.status_code}")
        return None
    except Exception as e:
        typer.echo(f"  [WARN] Error fetching historical price for {ticker}: {e}")
        return None


def fetch_sp500_performance(construction_date: date, current_date: date) -> Optional[dict]:
    """Fetch S&P 500 performance between two dates using yfinance.
    
    Returns dict with:
        - construction_price: S&P 500 price at construction date
        - current_price: S&P 500 price at current date
        - return_pct: Percentage return
    """
    try:
        # Use SPY ETF as proxy for S&P 500 (more reliable than ^GSPC)
        spy = yf.Ticker("SPY")
        
        # Fetch historical data
        hist = spy.history(start=construction_date, end=current_date + timedelta(days=1))
        
        if hist.empty:
            return None
        
        # Get price at construction date (first available date on or after construction)
        construction_price = None
        for date_idx in hist.index:
            if date_idx.date() >= construction_date:
                construction_price = float(hist.loc[date_idx, "Close"])
                break
        
        # Get current price (last available date)
        current_price = float(hist.iloc[-1]["Close"])
        
        if construction_price and current_price:
            return_pct = ((current_price - construction_price) / construction_price) * 100
            return {
                "construction_price": construction_price,
                "current_price": current_price,
                "return_pct": return_pct,
            }
        
        return None
    except Exception as e:
        typer.echo(f"  [WARN] Error fetching S&P 500 performance: {e}")
        return None
    except ValueError as e:
        typer.echo(f"  [WARN] Data conversion error fetching S&P 500 performance: {e}")
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
    
    # Validate construction date is not in the future
    if days_held < 0:
        typer.echo(f"[ERROR] Construction date {construction_date} is in the future")
        raise typer.Exit(code=1)
    
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
        # Note: use_stored_prices is not currently implemented as PortfolioHolding model
        # doesn't store price. This is a placeholder for future enhancement.
        construction_price = fetch_historical_price(
            holding.ticker, construction_date, cfg.fmp_api_key
        )
        time.sleep(0.25)  # Rate limit
        
        # Get current price
        current_price_data = fetch_price_data(
            holding.ticker, cfg.finnhub_api_key, cfg.fmp_api_key
        )
        current_price = current_price_data.price if current_price_data else None
        
        # Validate prices are positive numbers before calculating returns
        if construction_price and current_price:
            if construction_price <= 0 or current_price <= 0:
                typer.echo(f" [WARN - invalid price data]")
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
                continue
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
    
    # Fetch S&P 500 performance for comparison
    typer.echo("")
    typer.echo("Fetching S&P 500 performance for comparison...")
    sp500_perf = fetch_sp500_performance(construction_date, date.today())
    
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
    
    # S&P 500 comparison
    if sp500_perf:
        sp500_return = sp500_perf["return_pct"]
        outperformance = portfolio_return - sp500_return
        typer.echo("Benchmark Comparison (S&P 500):")
        typer.echo(f"  S&P 500 Return: {sp500_return:+.2f}%")
        typer.echo(f"  Portfolio Outperformance: {outperformance:+.2f}%")
        if outperformance > 0:
            typer.echo(f"  Portfolio beat S&P 500 by {outperformance:.2f} percentage points")
        else:
            typer.echo(f"  Portfolio underperformed S&P 500 by {abs(outperformance):.2f} percentage points")
        typer.echo("")
    else:
        typer.echo("Benchmark Comparison: S&P 500 data unavailable")
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
            "sp500_comparison": sp500_perf,
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
        
        # Add outperformance calculation if S&P 500 data available
        if sp500_perf:
            report["portfolio_metrics"]["sp500_return"] = sp500_perf["return_pct"]
            report["portfolio_metrics"]["outperformance"] = portfolio_return - sp500_perf["return_pct"]
        
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        typer.echo(f"\nDetailed report written to {out}")


@app.command()
def track(
    portfolio_file: Optional[Path] = typer.Option(
        None, help="Input portfolio JSON path (if not provided, evaluates all portfolios in run folders)"
    ),
    out: Optional[Path] = typer.Option(
        None, help="Output detailed report JSON path (optional, only used for single portfolio)"
    ),
    use_stored_prices: bool = typer.Option(
        False, help="Use stored prices from portfolio (if available)"
    ),
    evaluate_all: bool = typer.Option(
        True, help="Evaluate all portfolios in run folders (if portfolio_file not provided)"
    ),
):
    """Track portfolio performance since construction date.
    
    If portfolio_file is not provided and evaluate_all is True, evaluates all portfolios
    found in data/runs/ folders and saves performance reports for each.
    """
    if portfolio_file:
        # Single portfolio mode
        track_performance(portfolio_file, out, use_stored_prices)
    elif evaluate_all:
        # Multi-portfolio mode: find and evaluate all portfolios
        typer.echo("=" * 80)
        typer.echo("EVALUATING ALL PORTFOLIOS")
        typer.echo("=" * 80)
        typer.echo("")
        
        portfolios = find_all_portfolios()
        
        if not portfolios:
            typer.echo("No portfolios found in run folders.")
            typer.echo("Run 'python main.py portfolio build' first to create a portfolio.")
            raise typer.Exit(code=1)
        
        typer.echo(f"Found {len(portfolios)} portfolio(s) to evaluate:")
        for i, (port_path, constructed_at) in enumerate(portfolios, 1):
            typer.echo(f"  {i}. {port_path.parent.name} (constructed: {constructed_at.strftime('%Y-%m-%d %H:%M:%S')})")
        typer.echo("")
        
        # Evaluate each portfolio
        results = []
        for i, (port_path, constructed_at) in enumerate(portfolios, 1):
            typer.echo("")
            typer.echo("=" * 80)
            typer.echo(f"PORTFOLIO {i}/{len(portfolios)}: {port_path.parent.name}")
            typer.echo("=" * 80)
            typer.echo("")
            
            # Save performance report to the same run folder
            run_folder = port_path.parent
            perf_report_path = run_folder / "performance_report.json"
            
            try:
                track_performance(port_path, perf_report_path, use_stored_prices)
                results.append({
                    "portfolio": str(port_path),
                    "run_folder": str(run_folder),
                    "constructed_at": constructed_at.isoformat(),
                    "status": "success",
                    "report": str(perf_report_path),
                })
            except Exception as e:
                typer.echo(f"[ERROR] Failed to evaluate portfolio {port_path}: {e}")
                results.append({
                    "portfolio": str(port_path),
                    "run_folder": str(run_folder),
                    "constructed_at": constructed_at.isoformat(),
                    "status": "error",
                    "error": str(e),
                })
        
        # Print summary
        typer.echo("")
        typer.echo("=" * 80)
        typer.echo("EVALUATION SUMMARY")
        typer.echo("=" * 80)
        typer.echo(f"Total portfolios evaluated: {len(portfolios)}")
        typer.echo(f"Successful: {sum(1 for r in results if r['status'] == 'success')}")
        typer.echo(f"Failed: {sum(1 for r in results if r['status'] == 'error')}")
        typer.echo("")
        typer.echo("Performance reports saved to respective run folders.")
    else:
        typer.echo("Either provide --portfolio-file or use --evaluate-all (default)")
        raise typer.Exit(code=1)

