from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .config import load_config
from .openai_client import get_client, chat_json
from .prompts import system_universe, user_universe
from .models import CandidateResponse

app = typer.Typer(add_completion=False)


@app.command()
def generate(
    out: Path = typer.Option(Path("data/candidates.json"), help="Output JSON path"),
    count: Optional[int] = typer.Option(None, help="Candidate count override"),
    model: Optional[str] = typer.Option(None, help="OpenAI model override (defaults to cheap_model for efficiency)"),
):
    cfg = load_config()
    target_count = count or cfg.candidate_count
    # Use cheap model by default (simple generation task)
    chosen_model = model or cfg.cheap_model

    client = get_client()

    system = system_universe()
    user = user_universe(
        remaining_days=cfg.remaining_days,
        target_count=target_count,
        min_weight=cfg.min_weight,
        max_weight=cfg.max_weight,
        sector_cap=cfg.sector_cap,
        industry_cap=cfg.industry_cap,
        liquidity_dollar_min=cfg.min_avg_dollar_volume,
    )

    result = chat_json(client, chosen_model, system, user)

    try:
        parsed = CandidateResponse.model_validate(result)
    except Exception as e:
        typer.echo(f"Failed to parse candidates JSON: {e}")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding='utf-8')
        raise typer.Exit(code=1)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(parsed.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Wrote {len(parsed.candidates)} candidates -> {out}")


@app.command()
def merge(
    regular: Path = typer.Option(
        Path("data/candidates.json"), help="Regular candidates JSON path"
    ),
    themes: Path = typer.Option(
        Path("data/theme_candidates.json"), help="Theme candidates JSON path"
    ),
    out: Path = typer.Option(
        Path("data/merged_candidates.json"), help="Output merged JSON path"
    ),
    dedupe: bool = typer.Option(
        True, help="Remove duplicate tickers (keep theme-based if both exist)"
    ),
):
    """Merge regular and theme-based candidates."""
    if not regular.exists():
        typer.echo(f"Regular candidates file not found: {regular}")
        raise typer.Exit(code=1)

    if not themes.exists():
        typer.echo(f"Theme candidates file not found: {themes}")
        raise typer.Exit(code=1)

    regular_data = json.loads(regular.read_text())
    themes_data = json.loads(themes.read_text())

    try:
        regular_resp = CandidateResponse.model_validate(regular_data)
        themes_resp = CandidateResponse.model_validate(themes_data)
    except Exception as e:
        typer.echo(f"Failed to parse candidates: {e}")
        raise typer.Exit(code=1)

    # Build theme map for assigning themes to regular candidates
    theme_map = {cand.ticker: cand.theme for cand in themes_resp.candidates if cand.theme}
    
    if dedupe:
        # Build map of ticker -> candidate, theme candidates take precedence
        seen = {}
        for cand in regular_resp.candidates:
            # If this regular candidate doesn't have a theme but there's a theme candidate with the same ticker, assign the theme
            if cand.theme is None and cand.ticker in theme_map:
                from .models import Candidate
                cand = Candidate(
                    ticker=cand.ticker,
                    sector=cand.sector,
                    rationale=cand.rationale,
                    theme=theme_map[cand.ticker]
                )
            seen[cand.ticker] = cand
        # Theme candidates override regular ones if duplicate
        for cand in themes_resp.candidates:
            seen[cand.ticker] = cand
        merged_candidates = list(seen.values())
    else:
        # For regular candidates without themes, assign theme from theme_map if available
        regular_with_themes = []
        for cand in regular_resp.candidates:
            if cand.theme is None and cand.ticker in theme_map:
                # Create a new candidate with the theme assigned
                from .models import Candidate
                cand = Candidate(
                    ticker=cand.ticker,
                    sector=cand.sector,
                    rationale=cand.rationale,
                    theme=theme_map[cand.ticker]
                )
            regular_with_themes.append(cand)
        merged_candidates = list(regular_with_themes)
        merged_candidates.extend(themes_resp.candidates)

    merged = CandidateResponse(candidates=merged_candidates)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(merged.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(
        f"Merged {len(regular_resp.candidates)} regular + "
        f"{len(themes_resp.candidates)} theme candidates -> "
        f"{len(merged.candidates)} total -> {out}"
    )


def main():
    app()


if __name__ == "__main__":
    main()
