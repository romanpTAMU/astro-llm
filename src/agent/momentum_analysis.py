from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import typer
from openai import OpenAI

from .config import load_config
from .models import Portfolio, PortfolioHolding, ScoredCandidatesResponse

app = typer.Typer()


def fetch_7day_return(ticker: str, fmp_api_key: Optional[str]) -> Optional[float]:
    """Fetch 7-day return for a ticker using FMP API."""
    if not fmp_api_key:
        return None
    
    try:
        # Get current price
        quote_url = f"https://financialmodelingprep.com/api/v3/quote/{ticker}"
        quote_params = {"apikey": fmp_api_key}
        quote_resp = requests.get(quote_url, params=quote_params, timeout=10)
        
        if quote_resp.status_code != 200:
            return None
        
        quote_data = quote_resp.json()
        if not quote_data or not isinstance(quote_data, list) or len(quote_data) == 0:
            return None
        
        current_price = quote_data[0].get("price")
        if current_price is None:
            return None
        
        # Rate limit: wait before next API call
        time.sleep(0.25)
        
        # Get historical price (7 days ago)
        to_date = date.today()
        from_date = to_date - timedelta(days=10)  # Get a bit more to ensure we have data
        
        hist_url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
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
        if not historical or len(historical) < 2:
            return None
        
        # Historical data is typically sorted newest first, but check
        # Find price from 7 trading days ago (approximately)
        # We'll use the oldest price in the range as proxy for ~7 days ago
        if len(historical) >= 7:
            price_7d_ago = historical[-1].get("close")  # Oldest in range
        else:
            # Use oldest available
            price_7d_ago = historical[-1].get("close")
        
        if price_7d_ago is None or price_7d_ago == 0:
            return None
        
        # Calculate 7-day return
        return_7d = ((float(current_price) - float(price_7d_ago)) / float(price_7d_ago)) * 100
        
        return return_7d
    
    except Exception as e:
        typer.echo(f"  [WARN] Error fetching 7-day return for {ticker}: {e}")
        return None


def analyze_momentum(
    portfolio_file: Path,
    scored_file: Optional[Path] = None,
    out: Optional[Path] = None,
) -> None:
    """Analyze portfolio momentum tilt using 7-day returns."""
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
    
    typer.echo(f"Analyzing momentum for {len(portfolio.holdings)} portfolio holdings...")
    typer.echo("Fetching 7-day returns...")
    
    # Fetch 7-day returns for each holding
    returns_data = []
    for i, holding in enumerate(portfolio.holdings):
        typer.echo(f"  [{i+1}/{len(portfolio.holdings)}] {holding.ticker}...", nl=False)
        return_7d = fetch_7day_return(holding.ticker, cfg.fmp_api_key)
        
        if return_7d is not None:
            returns_data.append({
                "ticker": holding.ticker,
                "weight": holding.weight,
                "return_7d": return_7d,
                "sector": holding.sector,
                "composite_score": holding.composite_score,
            })
            typer.echo(f" {return_7d:+.2f}%")
        else:
            typer.echo(" [N/A]")
            returns_data.append({
                "ticker": holding.ticker,
                "weight": holding.weight,
                "return_7d": None,
                "sector": holding.sector,
                "composite_score": holding.composite_score,
            })
    
    # Calculate statistics
    valid_returns = [r for r in returns_data if r["return_7d"] is not None]
    
    if len(valid_returns) == 0:
        typer.echo("[ERROR] No valid 7-day returns fetched")
        raise typer.Exit(code=1)
    
    # Weighted average return
    weighted_return = sum(r["weight"] * r["return_7d"] for r in valid_returns)
    
    # Simple average return
    avg_return = sum(r["return_7d"] for r in valid_returns) / len(valid_returns)
    
    # Distribution
    returns_list = [r["return_7d"] for r in valid_returns]
    returns_list_sorted = sorted(returns_list)
    median_return = returns_list_sorted[len(returns_list_sorted) // 2]
    
    # Correlation between weights and returns
    weights = [r["weight"] for r in valid_returns]
    returns = [r["return_7d"] for r in valid_returns]
    
    # Calculate correlation coefficient
    if len(weights) > 1:
        mean_weight = sum(weights) / len(weights)
        mean_return = sum(returns) / len(returns)
        
        numerator = sum((w - mean_weight) * (r - mean_return) for w, r in zip(weights, returns))
        weight_var = sum((w - mean_weight) ** 2 for w in weights)
        return_var = sum((r - mean_return) ** 2 for r in returns)
        
        if weight_var > 0 and return_var > 0:
            correlation = numerator / (weight_var ** 0.5 * return_var ** 0.5)
        else:
            correlation = 0.0
    else:
        correlation = 0.0
    
    # Compare to universe average (if scored file provided)
    universe_avg_return = None
    if scored_file and scored_file.exists():
        try:
            scored_data = json.loads(scored_file.read_text(encoding='utf-8'))
            scored_resp = ScoredCandidatesResponse.model_validate(scored_data)
            
            # Fetch returns for top 20 by score as universe proxy
            top_universe = sorted(scored_resp.candidates, key=lambda x: x.composite_score, reverse=True)[:20]
            universe_returns = []
            
            typer.echo(f"\nFetching universe returns (top 20 by score)...")
            for i, stock in enumerate(top_universe):
                typer.echo(f"  [{i+1}/{len(top_universe)}] {stock.ticker}...", nl=False)
                return_7d = fetch_7day_return(stock.ticker, cfg.fmp_api_key)
                if return_7d is not None:
                    universe_returns.append(return_7d)
                    typer.echo(f" {return_7d:+.2f}%")
                else:
                    typer.echo(" [N/A]")
            
            if universe_returns:
                universe_avg_return = sum(universe_returns) / len(universe_returns)
        except Exception as e:
            typer.echo(f"[WARN] Could not analyze universe: {e}")
    
    # Print analysis
    typer.echo("\n" + "="*60)
    typer.echo("MOMENTUM ANALYSIS REPORT")
    typer.echo("="*60)
    
    typer.echo(f"\nPortfolio Statistics:")
    typer.echo(f"  Weighted Average 7-Day Return: {weighted_return:+.2f}%")
    typer.echo(f"  Simple Average 7-Day Return: {avg_return:+.2f}%")
    typer.echo(f"  Median 7-Day Return: {median_return:+.2f}%")
    typer.echo(f"  Min Return: {min(returns_list):+.2f}%")
    typer.echo(f"  Max Return: {max(returns_list):+.2f}%")
    
    if universe_avg_return is not None:
        typer.echo(f"\nUniverse Comparison (Top 20 by Score):")
        typer.echo(f"  Universe Average 7-Day Return: {universe_avg_return:+.2f}%")
        typer.echo(f"  Portfolio vs Universe: {weighted_return - universe_avg_return:+.2f}%")
    
    typer.echo(f"\nMomentum Tilt Analysis:")
    typer.echo(f"  Weight-Return Correlation: {correlation:+.3f}")
    if correlation > 0.3:
        typer.echo(f"  [WARN] Positive correlation suggests momentum tilt (higher weights on recent winners)")
    elif correlation < -0.3:
        typer.echo(f"  [INFO] Negative correlation suggests contrarian tilt (higher weights on recent losers)")
    else:
        typer.echo(f"  [OK] Low correlation suggests weights not strongly tied to recent returns")
    
    # Distribution by return buckets
    typer.echo(f"\nReturn Distribution:")
    positive_count = sum(1 for r in returns_list if r > 0)
    high_momentum = sum(1 for r in returns_list if r > 5)
    low_momentum = sum(1 for r in returns_list if r < -5)
    
    typer.echo(f"  Positive Returns: {positive_count}/{len(returns_list)} ({positive_count/len(returns_list)*100:.1f}%)")
    typer.echo(f"  High Momentum (>5%): {high_momentum} stocks")
    typer.echo(f"  Low Momentum (<-5%): {low_momentum} stocks")
    
    # Top and bottom performers
    typer.echo(f"\nTop 5 Momentum Winners (7-day return):")
    sorted_by_return = sorted(valid_returns, key=lambda x: x["return_7d"], reverse=True)
    for r in sorted_by_return[:5]:
        typer.echo(f"  {r['ticker']}: {r['return_7d']:+.2f}% (weight: {r['weight']*100:.1f}%)")
    
    typer.echo(f"\nBottom 5 Momentum Losers (7-day return):")
    for r in sorted_by_return[-5:]:
        typer.echo(f"  {r['ticker']}: {r['return_7d']:+.2f}% (weight: {r['weight']*100:.1f}%)")
    
    # Sector analysis
    sector_returns = defaultdict(list)
    for r in valid_returns:
        sector = r["sector"] or "Unknown"
        sector_returns[sector].append(r["return_7d"])
    
    typer.echo(f"\nSector Momentum (Average 7-Day Return):")
    for sector, returns in sorted(sector_returns.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True):
        avg_sector_return = sum(returns) / len(returns)
        typer.echo(f"  {sector}: {avg_sector_return:+.2f}% ({len(returns)} stocks)")
    
    # Summary assessment
    typer.echo(f"\n" + "="*60)
    typer.echo("ASSESSMENT:")
    typer.echo("="*60)
    
    if weighted_return > 5:
        typer.echo("[WARN] Portfolio shows strong recent momentum (>5% avg return)")
        typer.echo("  Consider: May be chasing recent winners")
    elif weighted_return > 2:
        typer.echo("[INFO] Portfolio shows moderate momentum (2-5% avg return)")
        typer.echo("  Consider: Some momentum exposure, likely acceptable")
    elif weighted_return > -2:
        typer.echo("[OK] Portfolio shows neutral momentum (-2% to +2% avg return)")
    else:
        typer.echo("[INFO] Portfolio shows negative momentum (<-2% avg return)")
        typer.echo("  Consider: May be contrarian or value-oriented")
    
    if correlation > 0.5:
        typer.echo("[WARN] Strong positive correlation between weights and returns")
        typer.echo("  Consider: Portfolio may be over-weighted in recent winners")
    elif correlation > 0.3:
        typer.echo("[INFO] Moderate positive correlation between weights and returns")
        typer.echo("  Consider: Some momentum bias, monitor for over-concentration")
    
    if high_momentum > 5:
        typer.echo(f"[WARN] {high_momentum} stocks with >5% 7-day returns")
        typer.echo("  Consider: May be over-exposed to momentum stocks")
    
    # Write detailed report if output file specified
    if out:
        report = {
            "analysis_date": datetime.now().isoformat(),
            "portfolio_file": str(portfolio_file),
            "statistics": {
                "weighted_avg_return": weighted_return,
                "simple_avg_return": avg_return,
                "median_return": median_return,
                "min_return": min(returns_list),
                "max_return": max(returns_list),
                "weight_return_correlation": correlation,
            },
            "universe_comparison": {
                "universe_avg_return": universe_avg_return,
                "portfolio_vs_universe": weighted_return - universe_avg_return if universe_avg_return else None,
            } if universe_avg_return else None,
            "distribution": {
                "positive_count": positive_count,
                "total_count": len(returns_list),
                "high_momentum_count": high_momentum,
                "low_momentum_count": low_momentum,
            },
            "holdings": returns_data,
            "sector_returns": {sector: sum(returns)/len(returns) for sector, returns in sector_returns.items()},
        }
        
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        typer.echo(f"\nDetailed report written to {out}")


@app.command()
def analyze(
    portfolio_file: Path = typer.Option(
        Path("data/portfolio.json"), help="Input portfolio JSON path"
    ),
    scored_file: Optional[Path] = typer.Option(
        None, help="Scored candidates file for universe comparison (optional)"
    ),
    out: Optional[Path] = typer.Option(
        None, help="Output detailed report JSON path (optional)"
    ),
):
    """Analyze portfolio momentum tilt using 7-day returns."""
    analyze_momentum(portfolio_file, scored_file, out)

