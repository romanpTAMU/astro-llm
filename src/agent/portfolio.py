from __future__ import annotations

import json
from collections import defaultdict
import math
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer
from openai import OpenAI

from .config import load_config
from .models import Portfolio, PortfolioHolding, ScoredCandidatesResponse
from .openai_client import chat_json, get_client
from .prompts import system_portfolio, user_portfolio
from .run_manager import RUN_MODE_FILE, get_run_folder

app = typer.Typer()


def calculate_sector_allocation(holdings: list[PortfolioHolding]) -> dict[str, float]:
    """Calculate sector allocation percentages."""
    sector_weights = defaultdict(float)
    for holding in holdings:
        sector = holding.sector or "Unknown"
        sector_weights[sector] += holding.weight
    return dict(sector_weights)


def _compute_sector_weights_percent(entries: list[dict]) -> dict[str, float]:
    """Helper to compute sector weights using float percentages (matches validation)."""
    weights: dict[str, float] = defaultdict(float)
    for entry in entries:
        sector = entry["holding"].sector or "Unknown"
        weights[sector] += entry["weight_percent"]
    return weights


def _enforce_min_max(entries: list[dict], min_percent: int, max_percent: int) -> None:
    """Ensure each entry stays within [min, max] percent ranges."""
    # Handle entries below minimum by taking weight from the largest positions
    deficits = [entry for entry in entries if entry["weight_percent"] < min_percent]
    for entry in deficits:
        deficit = min_percent - entry["weight_percent"]
        donors = sorted(
            [e for e in entries if e["weight_percent"] > min_percent],
            key=lambda e: e["weight_percent"],
            reverse=True,
        )
        for donor in donors:
            available = donor["weight_percent"] - min_percent
            if available <= 0:
                continue
            transfer = min(available, deficit)
            donor["weight_percent"] -= transfer
            entry["weight_percent"] += transfer
            deficit -= transfer
            if deficit == 0:
                break
        if deficit > 0:
            raise RuntimeError("Unable to satisfy minimum weight constraint while rounding.")

    # Handle entries above maximum by redistributing to smaller holdings
    excess_entries = [entry for entry in entries if entry["weight_percent"] > max_percent]
    for entry in excess_entries:
        excess = entry["weight_percent"] - max_percent
        entry["weight_percent"] = max_percent
        receivers = sorted(
            [e for e in entries if e is not entry and e["weight_percent"] < max_percent],
            key=lambda e: e["weight_percent"],
        )
        for receiver in receivers:
            room = max_percent - receiver["weight_percent"]
            if room <= 0:
                continue
            transfer = min(room, excess)
            receiver["weight_percent"] += transfer
            excess -= transfer
            if excess == 0:
                break
        if excess > 0:
            raise RuntimeError("Unable to redistribute excess weight while enforcing maximum constraint.")


def _round_weights_to_integers(entries: list[dict], min_percent: int, max_percent: int) -> None:
    """Round weight percentages to integers that sum to 100 while respecting min/max constraints."""
    raw_weights = [entry["weight_percent"] for entry in entries]
    floors = [int(math.floor(w)) for w in raw_weights]
    remainders = [w - f for w, f in zip(raw_weights, floors)]
    total = sum(floors)
    diff = 100 - total

    if diff > 0:
        ordered_indices = sorted(
            range(len(entries)),
            key=lambda i: (remainders[i], entries[i]["holding"].composite_score or 0),
            reverse=True,
        )
        idx = 0
        while diff > 0 and ordered_indices:
            i = ordered_indices[idx % len(ordered_indices)]
            if floors[i] < max_percent:
                floors[i] += 1
                diff -= 1
            idx += 1
        if diff > 0:
            # If we still have remaining diff because everyone hit max, just add to first entries
            for i in ordered_indices:
                floors[i] += 1
                diff -= 1
                if diff == 0:
                    break

    for entry, value in zip(entries, floors):
        entry["weight_percent"] = value

    _enforce_min_max(entries, min_percent, max_percent)

    # Final sanity adjustment to ensure total equals 100
    current_total = sum(entry["weight_percent"] for entry in entries)
    if current_total != 100:
        adjustment = 100 - current_total
        sortable = sorted(
            entries,
            key=lambda e: e["holding"].composite_score or 0,
            reverse=True,
        )
        for entry in sortable:
            new_value = entry["weight_percent"] + adjustment
            if min_percent <= new_value <= max_percent:
                entry["weight_percent"] = new_value
                adjustment = 0
                break
        if adjustment != 0:
            raise RuntimeError("Unable to normalize weights to 100%.")


def _rebalance_sector_caps(
    entries: list[dict],
    scored_resp: ScoredCandidatesResponse,
    selected_tickers: set[str],
    sector_cap_percent: int,
    min_percent: int,
) -> None:
    """Trim or swap holdings until all sector caps are satisfied."""
    sorted_candidates = sorted(
        scored_resp.candidates,
        key=lambda c: c.composite_score or 0,
        reverse=True,
    )
    
    # Convert to float for comparison (matches validation which uses float)
    sector_cap_float = float(sector_cap_percent)
    
    # Safety: prevent infinite loops
    max_iterations = 100
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        sector_weights = _compute_sector_weights_percent(entries)
        overweight_sectors = [
            (sector, weight - sector_cap_float)
            for sector, weight in sector_weights.items()
            if weight > sector_cap_float + 0.001  # Use same tolerance as validation
        ]
        if not overweight_sectors:
            break

        # Work on the most overweight sector first
        sector, over = max(overweight_sectors, key=lambda item: item[1])
        typer.echo(f"[INFO] Sector {sector} overweight by {over}% (cap {sector_cap_percent}%).")

        sector_entries = sorted(
            [entry for entry in entries if (entry["holding"].sector or "Unknown") == sector],
            key=lambda e: (e["weight_percent"], e["holding"].composite_score or 0),
        )

        # Trim the two lowest-weight names down to the minimum first
        for entry in sector_entries[:2]:
            reducible = entry["weight_percent"] - min_percent
            if reducible <= 0 or over <= 0:
                continue
            reduction = min(reducible, over)
            entry["weight_percent"] -= reduction
            over -= reduction
            typer.echo(
                f"  Reduced {entry['holding'].ticker} by {reduction}% -> {entry['weight_percent']}%."
            )

        if over <= 0:
            continue

        typer.echo("  Trimming insufficient; swapping out lowest-scoring name.")
        replace_entry = min(
            sector_entries,
            key=lambda e: (e["holding"].composite_score or 0, e["weight_percent"]),
        )
        removed_weight = replace_entry["weight_percent"]
        removed_ticker = replace_entry["holding"].ticker
        entries.remove(replace_entry)
        selected_tickers.discard(removed_ticker)
        sector_weights[sector] -= removed_weight
        typer.echo(
            f"  Removed {removed_ticker} ({removed_weight}%) from {sector} to free capacity."
        )

        replacement = None
        for cand in sorted_candidates:
            if cand.ticker in selected_tickers:
                continue
            cand_sector = cand.sector or "Unknown"
            if cand_sector == sector:
                continue
            cand_sector_weight = sector_weights.get(cand_sector, 0)
            if cand_sector_weight + removed_weight <= sector_cap_float + 0.001:  # Use same tolerance as validation
                replacement = cand
                break

        if replacement is None:
            raise RuntimeError(
                "Unable to find replacement candidate to satisfy sector caps."
            )

        new_holding = PortfolioHolding(
            ticker=replacement.ticker,
            weight=removed_weight / 100.0,
            sector=replacement.sector,
            theme=replacement.theme,
            rationale=f"Added during sector rebalance (score {replacement.composite_score:.3f})",
            composite_score=replacement.composite_score,
        )
        entries.append({"holding": new_holding, "weight_percent": removed_weight})
        selected_tickers.add(replacement.ticker)
        typer.echo(
            f"  Added {replacement.ticker} ({removed_weight}%) in sector {replacement.sector or 'Unknown'}."
        )
    
    if iteration >= max_iterations:
        raise RuntimeError(
            f"Unable to satisfy sector caps after {max_iterations} iterations. "
            "This may indicate insufficient candidate diversity across sectors."
        )


def enforce_sector_caps_and_integer_weights(
    holdings: list[PortfolioHolding],
    scored_resp: ScoredCandidatesResponse,
    selected_tickers: set[str],
    min_weight: float,
    max_weight: float,
    sector_cap: float,
) -> list[PortfolioHolding]:
    """Ensure weights are integer percentages, respect min/max bounds, and satisfy sector caps."""
    min_percent = int(round(min_weight * 100))
    max_percent = int(round(max_weight * 100))
    sector_cap_percent = int(round(sector_cap * 100))

    entries = [
        {"holding": holding, "weight_percent": holding.weight * 100.0}
        for holding in holdings
    ]

    _round_weights_to_integers(entries, min_percent, max_percent)
    _rebalance_sector_caps(
        entries,
        scored_resp,
        selected_tickers,
        sector_cap_percent,
        min_percent,
    )
    _round_weights_to_integers(entries, min_percent, max_percent)

    for entry in entries:
        entry["holding"].weight = entry["weight_percent"] / 100.0

    return [entry["holding"] for entry in entries]


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
    
    # Check sector caps (allow small tolerance after rebalancing attempts)
    # After rebalancing, allow up to 2% over cap to avoid blocking submission
    sector_allocation = calculate_sector_allocation(portfolio.holdings)
    sector_cap_tolerance = sector_cap + 0.02  # Allow 2% over cap after rebalancing
    for sector, weight in sector_allocation.items():
        if weight > sector_cap_tolerance:
            errors.append(f"Sector {sector}: {weight*100:.2f}% exceeds cap of {sector_cap*100:.0f}% (tolerance: {sector_cap_tolerance*100:.0f}%)")
        elif weight > sector_cap + 0.001:  # Warn if slightly over but within tolerance
            typer.echo(f"[WARN] Sector {sector}: {weight*100:.2f}% slightly over cap of {sector_cap*100:.0f}% but within tolerance")
    
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
            "news_summary": cand.news_summary,  # Include news summary
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
    
    # Save prompts and response for submission
    prompts_data = {
        "system_prompt": system,
        "user_prompt": user,
        "llm_response": result,
        "model": chosen_model,
        "timestamp": datetime.now().isoformat(),
    }
    
    # Save prompts file next to portfolio file
    prompts_file = Path(out_json).parent / "prompts_and_response.json"
    prompts_file.write_text(json.dumps(prompts_data, indent=2), encoding='utf-8')
    typer.echo(f"Saved prompts and response to {prompts_file}")
    
    # Parse and validate
    try:
        holdings_data = result.get("holdings", [])
        if len(holdings_data) != 20:
            typer.echo(f"[WARN] LLM returned {len(holdings_data)} holdings, expected 20")
        
        # Create holdings with composite scores from scored data
        holdings_map = {c.ticker: c for c in scored_resp.candidates}
        holdings = []
        selected_tickers = set()
        for h_data in holdings_data:
            ticker = h_data["ticker"]
            if ticker not in holdings_map:
                typer.echo(f"[WARN] Ticker {ticker} from LLM not found in scored candidates")
                continue
            
            selected_tickers.add(ticker)
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
        
        # If we have fewer than 20 holdings, add the next best candidates
        if len(holdings) < 20:
            missing_count = 20 - len(holdings)
            typer.echo(f"[WARN] Only {len(holdings)} holdings from LLM, adding {missing_count} top remaining candidates")
            
            # Calculate current total weight before adding
            current_total = sum(h.weight for h in holdings)
            
            # Sort all candidates by composite score (highest first)
            remaining_candidates = [
                c for c in scored_resp.candidates 
                if c.ticker not in selected_tickers
            ]
            remaining_candidates.sort(key=lambda x: x.composite_score or 0, reverse=True)
            
            # Calculate weight for new stocks such that after normalization they meet minimum
            # If we add M stocks with weight w each, total becomes T + M*w
            # After normalization: w / (T + M*w) >= min_weight
            # Solving: w >= (min_weight * T) / (1 - min_weight * M)
            if missing_count > 0 and cfg.min_weight * missing_count < 1.0:
                added_weight_per_stock = (cfg.min_weight * current_total) / (1.0 - cfg.min_weight * missing_count)
                # Ensure we don't exceed max_weight
                added_weight_per_stock = min(added_weight_per_stock, cfg.max_weight)
            else:
                # Fallback: use minimum weight
                added_weight_per_stock = cfg.min_weight
            
            # Add the top remaining candidates
            for i in range(min(missing_count, len(remaining_candidates))):
                cand = remaining_candidates[i]
                holding = PortfolioHolding(
                    ticker=cand.ticker,
                    weight=added_weight_per_stock,
                    sector=cand.sector,
                    theme=cand.theme,
                    rationale=f"Added as top remaining candidate (composite score: {cand.composite_score:.3f})",
                    composite_score=cand.composite_score,
                )
                holdings.append(holding)
                selected_tickers.add(cand.ticker)
                typer.echo(f"  Added {cand.ticker} (score: {cand.composite_score:.3f}, weight: {added_weight_per_stock:.4f})")
            
            # Normalize weights to sum to 1.0
            total_weight = sum(h.weight for h in holdings)
            if abs(total_weight - 1.0) > 0.001:  # Only normalize if significantly off
                typer.echo(f"[INFO] Normalizing weights from {total_weight:.6f} to 1.0")
                for holding in holdings:
                    holding.weight = holding.weight / total_weight
                
                # After normalization, ensure all weights meet minimum requirement
                # If any are below minimum, redistribute from holdings above minimum
                below_min = [h for h in holdings if h.weight < cfg.min_weight]
                if below_min:
                    typer.echo(f"[INFO] Adjusting {len(below_min)} holdings below minimum weight")
                    # Calculate total deficit
                    deficit = sum(cfg.min_weight - h.weight for h in below_min)
                    # Get holdings above minimum that we can reduce
                    above_min = [h for h in holdings if h.weight > cfg.min_weight]
                    
                    if above_min and deficit > 0:
                        # Calculate total weight we can reduce from above-min holdings
                        reducible = sum(h.weight - cfg.min_weight for h in above_min)
                        if reducible >= deficit:
                            # Reduce proportionally from above-min holdings
                            for h in below_min:
                                needed = cfg.min_weight - h.weight
                                # Reduce proportionally from above-min holdings
                                for ah in above_min:
                                    reduction = needed * (ah.weight - cfg.min_weight) / reducible
                                    ah.weight -= reduction
                                h.weight = cfg.min_weight
                            
                            # Re-normalize to ensure total is exactly 1.0
                            total_weight = sum(h.weight for h in holdings)
                            if abs(total_weight - 1.0) > 0.001:
                                for holding in holdings:
                                    holding.weight = holding.weight / total_weight
                        else:
                            typer.echo(f"[WARN] Cannot meet minimum weight requirement for all holdings (deficit: {deficit:.6f}, reducible: {reducible:.6f})")
        
        holdings = enforce_sector_caps_and_integer_weights(
            holdings,
            scored_resp,
            selected_tickers,
            cfg.min_weight,
            cfg.max_weight,
            cfg.sector_cap,
        )

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

    # Mark biweekly runs so they can be distinguished from daily runs
    run_folder = out_json.parent
    if run_folder.parent.name == "runs_biweekly":
        (run_folder / RUN_MODE_FILE).write_text(
            '{"mode": "biweekly"}', encoding="utf-8"
        )
        typer.echo(f"  Run mode: biweekly ({RUN_MODE_FILE})")
    
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
    out_json: Optional[Path] = typer.Option(
        None, help="Output portfolio JSON path (defaults to run folder)"
    ),
    out_excel: Optional[Path] = typer.Option(
        None, help="Output Excel file path (optional)"
    ),
    model: Optional[str] = typer.Option(
        None, help="OpenAI model override (defaults to openai_model)"
    ),
    use_run_folder: bool = typer.Option(
        True, help="Save to timestamped run folder (data/runs/YYYY-MM-DD_HH-MM-SS/)"
    ),
    runs_base_dir: Optional[Path] = typer.Option(
        None, help="Base directory for run folder (default: data/runs). Use data/runs_biweekly for biweekly mode."
    ),
):
    """Construct final portfolio from scored candidates."""
    import shutil
    
    # Create run folder if enabled
    if use_run_folder and out_json is None:
        base_dir = Path("data/runs") if runs_base_dir is None else Path(runs_base_dir)
        run_folder = get_run_folder(base_dir=base_dir)
        out_json = run_folder / "portfolio.json"
        
        # Copy intermediate files to run folder for reference
        typer.echo(f"Saving to run folder: {run_folder}")
        
        # Copy scored candidates
        if scored_file.exists():
            shutil.copy2(scored_file, run_folder / "scored_candidates.json")
            typer.echo(f"  Copied {scored_file.name} to run folder")
        
        # Try to find and copy other intermediate files
        candidates_file = Path("data/candidates.json")
        stock_data_file = Path("data/stock_data.json")
        theme_candidates_file = Path("data/theme_candidates.json")
        
        for src_file, dest_name in [
            (candidates_file, "candidates.json"),
            (stock_data_file, "stock_data.json"),
            (theme_candidates_file, "theme_candidates.json"),
        ]:
            if src_file.exists():
                shutil.copy2(src_file, run_folder / dest_name)
                typer.echo(f"  Copied {dest_name} to run folder")
    
    elif out_json is None:
        out_json = Path("data/portfolio.json")
    
    construct_portfolio(scored_file, out_json, out_excel, model)

