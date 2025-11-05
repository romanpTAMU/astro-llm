from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer
from openai import OpenAI

from .config import load_config
from .models import Portfolio, PortfolioHolding, ScoredCandidatesResponse
from .openai_client import chat_json, get_client
from .prompts import system_portfolio, user_portfolio

app = typer.Typer()


def calculate_sector_allocation(holdings: list[PortfolioHolding]) -> dict[str, float]:
    """Calculate sector allocation percentages."""
    sector_weights = defaultdict(float)
    for holding in holdings:
        sector = holding.sector or "Unknown"
        sector_weights[sector] += holding.weight
    return dict(sector_weights)


def validate_portfolio(
    portfolio: Portfolio,
    min_weight: float,
    max_weight: float,
    sector_cap: float,
    industry_cap: float,
    target_count: int = 20,
) -> tuple[bool, list[str]]:
    """Validate portfolio constraints."""
    errors = []
    
    # Check count
    if len(portfolio.holdings) != target_count:
        errors.append(f"Must have exactly {target_count} holdings, got {len(portfolio.holdings)}")
    
    # Check weights sum to 1.0
    total_weight = sum(h.weight for h in portfolio.holdings)
    if abs(total_weight - 1.0) > 0.001:  # Allow small floating point error
        errors.append(f"Weights must sum to 1.0, got {total_weight:.6f}")
    
    # Check individual weights
    for holding in portfolio.holdings:
        if holding.weight < min_weight:
            errors.append(f"{holding.ticker}: weight {holding.weight:.4f} below minimum {min_weight}")
        if holding.weight > max_weight:
            errors.append(f"{holding.ticker}: weight {holding.weight:.4f} above maximum {max_weight}")
    
    # Check sector caps
    sector_allocation = calculate_sector_allocation(portfolio.holdings)
    for sector, weight in sector_allocation.items():
        if weight > sector_cap:
            errors.append(f"Sector {sector}: {weight*100:.2f}% exceeds cap of {sector_cap*100:.0f}%")
    
    # Check industry caps (if we had industry data, for now just check we have it)
    # Industry allocation would need industry data from candidates
    
    return len(errors) == 0, errors


def construct_portfolio(
    scored_file: Path,
    out_json: Path,
    out_excel: Optional[Path] = None,
    model: Optional[str] = None,
) -> Portfolio:
    """Construct portfolio from scored candidates."""
    cfg = load_config()
    chosen_model = model or cfg.openai_model
    
    # Load scored candidates
    if not scored_file.exists():
        typer.echo(f"Scored candidates file not found: {scored_file}")
        raise typer.Exit(code=1)
    
    scored_data = json.loads(scored_file.read_text(encoding='utf-8'))
    try:
        scored_resp = ScoredCandidatesResponse.model_validate(scored_data)
    except Exception as e:
        typer.echo(f"Failed to parse scored candidates file: {e}")
        raise typer.Exit(code=1)
    
    if len(scored_resp.candidates) < 20:
        typer.echo(f"Need at least 20 scored candidates, got {len(scored_resp.candidates)}")
        raise typer.Exit(code=1)
    
    typer.echo(f"Constructing portfolio from {len(scored_resp.candidates)} scored candidates...")
    
    # Prepare candidate data for LLM
    candidates_dict = []
    for cand in scored_resp.candidates:
        cand_dict = {
            "ticker": cand.ticker,
            "sector": cand.sector,
            "theme": cand.theme,
            "composite_score": cand.composite_score,
            "price": cand.price,
            "sentiment": {
                "overall_sentiment": cand.sentiment.overall_sentiment,
                "sentiment_score": cand.sentiment.sentiment_score,
            } if cand.sentiment else {},
        }
        candidates_dict.append(cand_dict)
    
    # Call LLM to construct portfolio
    client = get_client()
    system = system_portfolio()
    user = user_portfolio(
        scored_candidates=candidates_dict,
        remaining_days=cfg.remaining_days,
        min_weight=cfg.min_weight,
        max_weight=cfg.max_weight,
        sector_cap=cfg.sector_cap,
        industry_cap=cfg.industry_cap,
        horizon_end=cfg.portfolio_horizon_end,
    )
    
    typer.echo("Calling LLM to construct portfolio...")
    result = chat_json(client, chosen_model, system, user, timeout=180.0)
    
    # Parse and validate
    try:
        holdings_data = result.get("holdings", [])
        if len(holdings_data) != 20:
            typer.echo(f"[WARN] LLM returned {len(holdings_data)} holdings, expected 20")
        
        # Create holdings with composite scores from scored data
        holdings_map = {c.ticker: c for c in scored_resp.candidates}
        holdings = []
        for h_data in holdings_data:
            ticker = h_data["ticker"]
            if ticker not in holdings_map:
                typer.echo(f"[WARN] Ticker {ticker} from LLM not found in scored candidates")
                continue
            
            scored_stock = holdings_map[ticker]
            holding = PortfolioHolding(
                ticker=ticker,
                weight=float(h_data["weight"]),
                sector=h_data.get("sector") or scored_stock.sector,
                theme=h_data.get("theme") or scored_stock.theme,
                rationale=h_data.get("rationale"),
                composite_score=scored_stock.composite_score,
            )
            holdings.append(holding)
        
        if len(holdings) != 20:
            typer.echo(f"[ERROR] Only {len(holdings)} valid holdings after parsing, need 20")
            raise typer.Exit(code=1)
        
        # Calculate allocations
        sector_allocation = calculate_sector_allocation(holdings)
        
        portfolio = Portfolio(
            holdings=holdings,
            total_weight=sum(h.weight for h in holdings),
            sector_allocation=sector_allocation,
            industry_allocation={},  # Would need industry data
            portfolio_date=date.today(),
            horizon_end=cfg.portfolio_horizon_end,
            constructed_at=datetime.now(),
        )
        
        # Validate
        is_valid, errors = validate_portfolio(
            portfolio,
            cfg.min_weight,
            cfg.max_weight,
            cfg.sector_cap,
            cfg.industry_cap,
        )
        
        if not is_valid:
            typer.echo("[ERROR] Portfolio validation failed:")
            for error in errors:
                typer.echo(f"  - {error}")
            raise typer.Exit(code=1)
        
        typer.echo("[OK] Portfolio validation passed")
        
    except Exception as e:
        typer.echo(f"Failed to parse portfolio from LLM response: {e}")
        typer.echo(f"LLM response: {json.dumps(result, indent=2)}")
        raise typer.Exit(code=1)
    
    # Write JSON
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(portfolio.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Portfolio written to {out_json}")
    
    # Write Excel if requested
    if out_excel:
        try:
            import pandas as pd
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment
            from openpyxl.utils import get_column_letter
            
            # Create DataFrame
            df_data = []
            for holding in portfolio.holdings:
                df_data.append({
                    "Ticker": holding.ticker,
                    "Weight (%)": holding.weight * 100,
                    "Sector": holding.sector or "Unknown",
                    "Theme": holding.theme or "None",
                    "Composite Score": holding.composite_score or 0.0,
                    "Rationale": holding.rationale or "",
                })
            
            df = pd.DataFrame(df_data)
            df = df.sort_values("Weight (%)", ascending=False)
            
            # Write to Excel
            with pd.ExcelWriter(out_excel, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Portfolio', index=False)
                
                # Format worksheet
                worksheet = writer.sheets['Portfolio']
                
                # Header formatting
                for cell in worksheet[1]:
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal='center')
                
                # Auto-adjust column widths
                for idx, col in enumerate(df.columns, 1):
                    max_length = max(
                        df[col].astype(str).map(len).max(),
                        len(col)
                    )
                    worksheet.column_dimensions[get_column_letter(idx)].width = min(max_length + 2, 50)
                
                # Add summary sheet
                summary_data = {
                    "Metric": [
                        "Total Holdings",
                        "Total Weight (%)",
                        "Portfolio Date",
                        "Horizon End",
                    ],
                    "Value": [
                        len(portfolio.holdings),
                        portfolio.total_weight * 100,
                        portfolio.portfolio_date.strftime("%Y-%m-%d"),
                        portfolio.horizon_end.strftime("%Y-%m-%d"),
                    ],
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
                
                # Add sector allocation sheet
                sector_df = pd.DataFrame({
                    "Sector": list(portfolio.sector_allocation.keys()),
                    "Weight (%)": [w * 100 for w in portfolio.sector_allocation.values()],
                })
                sector_df = sector_df.sort_values("Weight (%)", ascending=False)
                sector_df.to_excel(writer, sheet_name='Sector Allocation', index=False)
            
            typer.echo(f"Excel file written to {out_excel}")
        except ImportError:
            typer.echo("[WARN] pandas/openpyxl not installed, skipping Excel export")
        except Exception as e:
            typer.echo(f"[WARN] Failed to write Excel file: {e}")
    
    # Print summary
    typer.echo("\nPortfolio Summary:")
    typer.echo(f"  Holdings: {len(portfolio.holdings)}")
    typer.echo(f"  Total Weight: {portfolio.total_weight*100:.2f}%")
    typer.echo(f"  Sector Allocation:")
    for sector, weight in sorted(portfolio.sector_allocation.items(), key=lambda x: x[1], reverse=True):
        typer.echo(f"    {sector}: {weight*100:.2f}%")
    typer.echo(f"\nTop 5 Holdings:")
    sorted_holdings = sorted(portfolio.holdings, key=lambda x: x.weight, reverse=True)
    for holding in sorted_holdings[:5]:
        typer.echo(f"  {holding.ticker}: {holding.weight*100:.2f}%")
    
    return portfolio


@app.command()
def build(
    scored_file: Path = typer.Option(
        Path("data/scored_candidates.json"), help="Input scored candidates JSON path"
    ),
    out_json: Path = typer.Option(
        Path("data/portfolio.json"), help="Output portfolio JSON path"
    ),
    out_excel: Optional[Path] = typer.Option(
        None, help="Output Excel file path (optional)"
    ),
    model: Optional[str] = typer.Option(
        None, help="OpenAI model override (defaults to openai_model)"
    ),
):
    """Construct final portfolio from scored candidates."""
    construct_portfolio(scored_file, out_json, out_excel, model)

