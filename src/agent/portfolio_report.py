from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import typer

from .models import Portfolio

app = typer.Typer()


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

