from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

import typer

from .config import load_config
from .data_fetcher import fetch_price_data
from .models import Portfolio, ScoredCandidatesResponse

app = typer.Typer()


def _load_portfolio_and_prices(
    portfolio_file: Path,
    scored_candidates_file: Optional[Path],
) -> tuple[Portfolio, dict[str, float]]:
    """Load portfolio and price-by-ticker from scored candidates."""
    if scored_candidates_file is None:
        scored_candidates_file = portfolio_file.parent / "scored_candidates.json"
    if not scored_candidates_file.exists():
        typer.echo(f"Scored candidates file not found: {scored_candidates_file}")
        raise typer.Exit(code=1)
    portfolio_data = json.loads(portfolio_file.read_text(encoding="utf-8"))
    portfolio = Portfolio.model_validate(portfolio_data)
    scored_data = json.loads(scored_candidates_file.read_text(encoding="utf-8"))
    scored_resp = ScoredCandidatesResponse.model_validate(scored_data)
    price_by_ticker = {
        c.ticker: c.price
        for c in scored_resp.candidates
        if c.price is not None and c.price > 0
    }
    return portfolio, price_by_ticker


def write_trades_csv(
    portfolio_file: Path,
    out_csv: Path,
    scored_candidates_file: Optional[Path] = None,
    notional: float = 1_000_000.0,
    side: str = "Buy",
    previous_portfolio_file: Optional[Path] = None,
    previous_scored_file: Optional[Path] = None,
) -> None:
    """Write a trades CSV with columns B/S, SYMBOL, QTY, PRICE, PRINCIPAL.

    - Initial run (no previous): all rows are Buy at full target size.
    - Rebalance (previous provided): Sell rows first (reductions/removals), then Buy rows
      (additions/increases), so the sheet rebalances the existing portfolio to the new one.
    """
    if not portfolio_file.exists():
        typer.echo(f"Portfolio file not found: {portfolio_file}")
        raise typer.Exit(code=1)

    portfolio, price_by_ticker = _load_portfolio_and_prices(
        portfolio_file, scored_candidates_file
    )

    def _shares(weight: float, ticker: str) -> int:
        p = price_by_ticker.get(ticker)
        if p is None or p <= 0:
            return 0
        return int(round(weight * notional / p))

    # Current target shares per ticker
    current_shares = {}
    for h in portfolio.holdings:
        q = _shares(h.weight, h.ticker)
        if q > 0:
            current_shares[h.ticker] = q

    if previous_portfolio_file is None or not previous_portfolio_file.exists():
        # Initial run: all Buy at full size
        rows = []
        for holding in portfolio.holdings:
            price = price_by_ticker.get(holding.ticker)
            if price is None or price <= 0:
                typer.echo(f"[WARN] No valid price for {holding.ticker}, skipping row")
                continue
            qty = current_shares.get(holding.ticker, 0)
            if qty <= 0:
                continue
            principal = round(qty * price, 2)
            rows.append({
                "B/S": "Buy",
                "SYMBOL": holding.ticker,
                "QTY": qty,
                "PRICE": round(price, 4),
                "PRINCIPAL": principal,
            })
    else:
        # Rebalance: compare to previous portfolio
        prev_portfolio, prev_prices = _load_portfolio_and_prices(
            previous_portfolio_file,
            previous_scored_file or (previous_portfolio_file.parent / "scored_candidates.json"),
        )

        def _prev_shares(weight: float, ticker: str) -> int:
            p = prev_prices.get(ticker)
            if p is None or p <= 0:
                return 0
            return int(round(weight * notional / p))

        prev_shares = {}
        for h in prev_portfolio.holdings:
            q = _prev_shares(h.weight, h.ticker)
            if q > 0:
                prev_shares[h.ticker] = q

        # Fetch current price for sell-only tickers (not in current run's scored data)
        sell_only_tickers = set(prev_shares) - set(price_by_ticker)
        if sell_only_tickers:
            cfg = load_config()
            for ticker in sell_only_tickers:
                typer.echo(f"  Fetching current price for sell-only {ticker}...", nl=False)
                pd = fetch_price_data(
                    ticker,
                    finnhub_key=cfg.finnhub_api_key,
                    fmp_key=cfg.fmp_api_key,
                )
                if pd and pd.price and pd.price > 0:
                    price_by_ticker[ticker] = pd.price
                    typer.echo(f" ${pd.price:.2f}")
                else:
                    typer.echo(" N/A (using previous run price)")
                    if prev_prices.get(ticker):
                        price_by_ticker[ticker] = prev_prices[ticker]
                time.sleep(0.25)

        # Portfolio value before rebalance (at current prices); P&L = value - target notional
        portfolio_value_before_rebalance = 0.0
        for ticker, qty in prev_shares.items():
            price = price_by_ticker.get(ticker) or prev_prices.get(ticker)
            if price and price > 0:
                portfolio_value_before_rebalance += qty * price
        period_pnl = portfolio_value_before_rebalance - notional

        all_tickers = set(prev_shares) | set(current_shares)
        sell_rows = []
        buy_rows = []
        for ticker in sorted(all_tickers):
            prev_q = prev_shares.get(ticker, 0)
            curr_q = current_shares.get(ticker, 0)
            # Use current price (from scored data or fetched for sell-only); else previous price
            price = price_by_ticker.get(ticker) or prev_prices.get(ticker)
            if price is None or price <= 0:
                if prev_q > 0:
                    typer.echo(f"[WARN] No price for {ticker}, skipping row")
                continue
            if curr_q > prev_q:
                buy_rows.append({
                    "B/S": "Buy",
                    "SYMBOL": ticker,
                    "QTY": curr_q - prev_q,
                    "PRICE": round(price, 4),
                    "PRINCIPAL": round((curr_q - prev_q) * price, 2),
                })
            elif curr_q < prev_q:
                sell_rows.append({
                    "B/S": "Sell",
                    "SYMBOL": ticker,
                    "QTY": prev_q - curr_q,
                    "PRICE": round(price, 4),
                    "PRINCIPAL": round((prev_q - curr_q) * price, 2),
                })

        rows = sell_rows + buy_rows

        # Write period P&L for this rebalance (reset to notional each time; P&L taken out)
        pnl_file = out_csv.parent / "period_pnl.json"
        pnl_data = {
            "period_pnl": round(period_pnl, 2),
            "portfolio_value_before_rebalance": round(portfolio_value_before_rebalance, 2),
            "target_notional": notional,
            "run_date": date.today().isoformat(),
        }
        pnl_file.write_text(json.dumps(pnl_data, indent=2), encoding="utf-8")
        typer.echo(
            f"  Period P&L: ${period_pnl:+,.2f} (value before rebalance: ${portfolio_value_before_rebalance:,.2f} → target ${notional:,.0f})"
        )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["B/S", "SYMBOL", "QTY", "PRICE", "PRINCIPAL"])
        writer.writeheader()
        writer.writerows(rows)

    typer.echo(f"Trades CSV written to {out_csv} ({len(rows)} rows)")


def generate_portfolio_report(
    portfolio_file: Path,
    out: Path,
    format: str = "markdown",
) -> None:
    """Generate human-readable portfolio report."""
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
    
    # Sort holdings by weight (descending)
    sorted_holdings = sorted(portfolio.holdings, key=lambda x: x.weight, reverse=True)
    
    # Calculate statistics
    total_weight = sum(h.weight for h in portfolio.holdings)
    
    # Sector allocation
    sector_allocation = defaultdict(float)
    sector_holdings = defaultdict(list)
    for holding in portfolio.holdings:
        sector = holding.sector or "Unknown"
        sector_allocation[sector] += holding.weight
        sector_holdings[sector].append(holding)
    
    # Theme distribution
    theme_count = defaultdict(int)
    for holding in portfolio.holdings:
        theme = holding.theme or "None"
        theme_count[theme] += 1
    
    # Score statistics
    scores = [h.composite_score for h in portfolio.holdings if h.composite_score is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    min_score = min(scores) if scores else 0.0
    max_score = max(scores) if scores else 0.0
    
    # Generate report
    if format == "markdown":
        content = generate_markdown_report(
            portfolio, sorted_holdings, sector_allocation, theme_count,
            avg_score, min_score, max_score, total_weight
        )
    elif format == "text":
        content = generate_text_report(
            portfolio, sorted_holdings, sector_allocation, theme_count,
            avg_score, min_score, max_score, total_weight
        )
    else:
        typer.echo(f"Unknown format: {format}. Use 'markdown' or 'text'")
        raise typer.Exit(code=1)
    
    # Write file
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding='utf-8')
    typer.echo(f"Portfolio report written to {out}")


def generate_markdown_report(
    portfolio: Portfolio,
    sorted_holdings: list,
    sector_allocation: dict,
    theme_count: dict,
    avg_score: float,
    min_score: float,
    max_score: float,
    total_weight: float,
) -> str:
    """Generate Markdown-formatted report."""
    lines = []
    
    # Header
    lines.append("# Portfolio Report")
    lines.append("")
    lines.append(f"**Portfolio Date:** {portfolio.portfolio_date.strftime('%B %d, %Y')}")
    lines.append(f"**Investment Horizon:** Through {portfolio.horizon_end.strftime('%B %d, %Y')}")
    lines.append(f"**Total Holdings:** {len(portfolio.holdings)}")
    lines.append(f"**Total Weight:** {total_weight*100:.2f}%")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Holdings section
    lines.append("## Portfolio Holdings")
    lines.append("")
    lines.append("Sorted by weight (highest to lowest)")
    lines.append("")
    lines.append("| # | Ticker | Weight | Sector | Theme | Rationale |")
    lines.append("|---|--------|-------|--------|-------|-----------|")
    
    for i, holding in enumerate(sorted_holdings, 1):
        weight_pct = holding.weight * 100
        sector = holding.sector or "Unknown"
        theme = holding.theme or "None"
        if len(theme) > 50:
            theme = theme[:47] + "..."
        rationale = holding.rationale or "N/A"
        if len(rationale) > 80:
            rationale = rationale[:77] + "..."
        
        lines.append(f"| {i} | **{holding.ticker}** | {weight_pct:.2f}% | {sector} | {theme} | {rationale} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Detailed holdings (without table constraints)
    lines.append("## Detailed Holdings")
    lines.append("")
    
    for i, holding in enumerate(sorted_holdings, 1):
        weight_pct = holding.weight * 100
        lines.append(f"### {i}. {holding.ticker} ({weight_pct:.2f}%)")
        lines.append("")
        
        if holding.sector:
            lines.append(f"**Sector:** {holding.sector}")
        if holding.theme:
            lines.append(f"**Theme:** {holding.theme}")
        if holding.composite_score is not None:
            lines.append(f"**Composite Score:** {holding.composite_score:.3f}")
        if holding.rationale:
            lines.append(f"**Rationale:** {holding.rationale}")
        
        lines.append("")
    
    lines.append("---")
    lines.append("")
    
    # Summary statistics
    lines.append("## Summary Statistics")
    lines.append("")
    
    lines.append("### Sector Allocation")
    lines.append("")
    lines.append("| Sector | Weight | Holdings |")
    lines.append("|--------|--------|----------|")
    for sector, weight in sorted(sector_allocation.items(), key=lambda x: x[1], reverse=True):
        count = len([h for h in portfolio.holdings if (h.sector or "Unknown") == sector])
        lines.append(f"| {sector} | {weight*100:.2f}% | {count} |")
    
    lines.append("")
    lines.append("### Theme Distribution")
    lines.append("")
    lines.append("| Theme | Count |")
    lines.append("|-------|-------|")
    for theme, count in sorted(theme_count.items(), key=lambda x: x[1], reverse=True):
        if len(theme) > 60:
            theme_display = theme[:57] + "..."
        else:
            theme_display = theme
        lines.append(f"| {theme_display} | {count} |")
    
    lines.append("")
    lines.append("### Composite Score Statistics")
    lines.append("")
    lines.append(f"- **Average Score:** {avg_score:.3f}")
    lines.append(f"- **Min Score:** {min_score:.3f}")
    lines.append(f"- **Max Score:** {max_score:.3f}")
    lines.append(f"- **Score Range:** {max_score - min_score:.3f}")
    
    lines.append("")
    lines.append("### Weight Distribution")
    lines.append("")
    weight_buckets = {
        "8-10%": sum(1 for h in portfolio.holdings if 0.08 <= h.weight <= 0.10),
        "6-8%": sum(1 for h in portfolio.holdings if 0.06 <= h.weight < 0.08),
        "4-6%": sum(1 for h in portfolio.holdings if 0.04 <= h.weight < 0.06),
        "2-4%": sum(1 for h in portfolio.holdings if 0.02 <= h.weight < 0.04),
    }
    for bucket, count in weight_buckets.items():
        lines.append(f"- **{bucket}:** {count} holdings")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*Report generated on {date.today().strftime('%B %d, %Y')}*")
    
    return "\n".join(lines)


def generate_text_report(
    portfolio: Portfolio,
    sorted_holdings: list,
    sector_allocation: dict,
    theme_count: dict,
    avg_score: float,
    min_score: float,
    max_score: float,
    total_weight: float,
) -> str:
    """Generate plain text-formatted report."""
    lines = []
    
    # Header
    lines.append("=" * 80)
    lines.append("PORTFOLIO REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"Portfolio Date: {portfolio.portfolio_date.strftime('%B %d, %Y')}")
    lines.append(f"Investment Horizon: Through {portfolio.horizon_end.strftime('%B %d, %Y')}")
    lines.append(f"Total Holdings: {len(portfolio.holdings)}")
    lines.append(f"Total Weight: {total_weight*100:.2f}%")
    lines.append("")
    lines.append("=" * 80)
    lines.append("")
    
    # Holdings section
    lines.append("PORTFOLIO HOLDINGS")
    lines.append("-" * 80)
    lines.append("Sorted by weight (highest to lowest)")
    lines.append("")
    
    for i, holding in enumerate(sorted_holdings, 1):
        weight_pct = holding.weight * 100
        lines.append(f"{i}. {holding.ticker} ({weight_pct:.2f}%)")
        lines.append(f"   Sector: {holding.sector or 'Unknown'}")
        if holding.theme:
            lines.append(f"   Theme: {holding.theme}")
        if holding.composite_score is not None:
            lines.append(f"   Composite Score: {holding.composite_score:.3f}")
        if holding.rationale:
            lines.append(f"   Rationale: {holding.rationale}")
        lines.append("")
    
    lines.append("=" * 80)
    lines.append("")
    
    # Summary statistics
    lines.append("SUMMARY STATISTICS")
    lines.append("-" * 80)
    lines.append("")
    
    lines.append("Sector Allocation:")
    for sector, weight in sorted(sector_allocation.items(), key=lambda x: x[1], reverse=True):
        count = len([h for h in portfolio.holdings if (h.sector or "Unknown") == sector])
        lines.append(f"  {sector}: {weight*100:.2f}% ({count} holdings)")
    
    lines.append("")
    lines.append("Theme Distribution:")
    for theme, count in sorted(theme_count.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {theme}: {count} holdings")
    
    lines.append("")
    lines.append("Composite Score Statistics:")
    lines.append(f"  Average: {avg_score:.3f}")
    lines.append(f"  Min: {min_score:.3f}")
    lines.append(f"  Max: {max_score:.3f}")
    lines.append(f"  Range: {max_score - min_score:.3f}")
    
    lines.append("")
    lines.append("Weight Distribution:")
    weight_buckets = {
        "8-10%": sum(1 for h in portfolio.holdings if 0.08 <= h.weight <= 0.10),
        "6-8%": sum(1 for h in portfolio.holdings if 0.06 <= h.weight < 0.08),
        "4-6%": sum(1 for h in portfolio.holdings if 0.04 <= h.weight < 0.06),
        "2-4%": sum(1 for h in portfolio.holdings if 0.02 <= h.weight < 0.04),
    }
    for bucket, count in weight_buckets.items():
        lines.append(f"  {bucket}: {count} holdings")
    
    lines.append("")
    lines.append("=" * 80)
    lines.append(f"Report generated on {date.today().strftime('%B %d, %Y')}")
    
    return "\n".join(lines)


@app.command()
def generate(
    portfolio_file: Path = typer.Option(
        Path("data/portfolio.json"), help="Input portfolio JSON path"
    ),
    out: Path = typer.Option(
        Path("data/portfolio_report.md"), help="Output report file path"
    ),
    format: str = typer.Option(
        "markdown", help="Output format: 'markdown' or 'text'"
    ),
):
    """Generate human-readable portfolio report."""
    # Determine format from file extension if not specified
    if format == "markdown" and out.suffix.lower() not in [".md", ".markdown"]:
        if out.suffix.lower() == ".txt":
            format = "text"
    
    generate_portfolio_report(portfolio_file, out, format)


@app.command("trades-csv")
def trades_csv(
    portfolio_file: Path = typer.Option(
        ..., help="Portfolio JSON path (e.g. run folder / portfolio.json)"
    ),
    out: Path = typer.Option(
        ..., help="Output CSV path (e.g. run folder / trades.csv)"
    ),
    scored_candidates_file: Optional[Path] = typer.Option(
        None, help="Scored candidates JSON (default: same dir as portfolio / scored_candidates.json)"
    ),
    notional: float = typer.Option(
        1_000_000.0, help="Total notional in USD for computing QTY"
    ),
    side: str = typer.Option(
        "Buy", help="B/S column value when not rebalancing (ignored if --previous-run is set)"
    ),
    previous_run: Optional[Path] = typer.Option(
        None,
        "--previous-run",
        help="Path to previous run folder for rebalance: Sells then Buys to adjust to current portfolio",
    ),
):
    """Export portfolio to a trades CSV (B/S, SYMBOL, QTY, PRICE, PRINCIPAL).
    With --previous-run, outputs rebalance trades (sells then buys) for a single continuous portfolio.
    """
    prev_portfolio = prev_scored = None
    if previous_run is not None:
        prev_portfolio = previous_run / "portfolio.json"
        prev_scored = previous_run / "scored_candidates.json"
        if not prev_portfolio.exists():
            typer.echo(f"[ERROR] Previous run portfolio not found: {prev_portfolio}")
            raise typer.Exit(code=1)
    write_trades_csv(
        portfolio_file=portfolio_file,
        out_csv=out,
        scored_candidates_file=scored_candidates_file,
        notional=notional,
        side=side,
        previous_portfolio_file=prev_portfolio,
        previous_scored_file=prev_scored if (prev_scored and prev_scored.exists()) else None,
    )

