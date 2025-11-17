from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .config import load_config
from .openai_client import get_client, chat_json
from .data_apis import fetch_general_news_fmp
from .prompts import (
    system_themes,
    user_themes,
    system_theme_candidates,
    user_theme_candidates,
)
from .models import ThemeResponse, CandidateResponse

app = typer.Typer(add_completion=False)


@app.command()
def identify(
    out: Path = typer.Option(Path("data/themes.json"), help="Output JSON path"),
    model: Optional[str] = typer.Option(None, help="OpenAI model override (defaults to cheap_model for efficiency)"),
):
    """Identify major market themes using recent news and market analysis."""
    cfg = load_config()
    # Use cheap model by default (simple identification task)
    chosen_model = model or cfg.cheap_model

    client = get_client()
    
    # Fetch recent general news to inform theme identification
    general_news = []
    if cfg.fmp_api_key:
        typer.echo("Fetching recent market news to inform theme identification...")
        general_news = fetch_general_news_fmp(cfg.fmp_api_key, max_items=50)
        typer.echo(f"  Fetched {len(general_news)} recent news articles")
    else:
        typer.echo("[WARN] No FMP API key - theme identification will proceed without news data")

    system = system_themes()
    user = user_themes(cfg.portfolio_horizon_end, cfg.remaining_days, general_news)

    result = chat_json(client, chosen_model, system, user)

    try:
        parsed = ThemeResponse.model_validate(result)
    except Exception as e:
        typer.echo(f"Failed to parse themes JSON: {e}")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding='utf-8')
        raise typer.Exit(code=1)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(parsed.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Identified {len(parsed.themes)} themes -> {out}")


@app.command()
def generate_candidates(
    themes_file: Path = typer.Option(
        Path("data/themes.json"), help="Input themes JSON path"
    ),
    out: Path = typer.Option(
        Path("data/theme_candidates.json"), help="Output JSON path"
    ),
    model: Optional[str] = typer.Option(None, help="OpenAI model override (defaults to cheap_model for efficiency)"),
    batch_size: int = typer.Option(3, help="Number of themes to process per batch"),
):
    """Generate stock candidates based on identified themes."""
    cfg = load_config()
    # Use cheap model by default (simple generation task)
    chosen_model = model or cfg.cheap_model

    if not themes_file.exists():
        typer.echo(f"Themes file not found: {themes_file}")
        typer.echo("Run 'identify' command first to generate themes.")
        raise typer.Exit(code=1)

    themes_data = json.loads(themes_file.read_text())
    try:
        themes_resp = ThemeResponse.model_validate(themes_data)
    except Exception as e:
        typer.echo(f"Failed to parse themes file: {e}")
        raise typer.Exit(code=1)

    themes_list = [t.model_dump() for t in themes_resp.themes]
    client = get_client()
    system = system_theme_candidates()
    
    # Process themes in batches to avoid timeout
    all_candidates = []
    total_themes = len(themes_list)
    
    for i in range(0, total_themes, batch_size):
        batch = themes_list[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (total_themes + batch_size - 1) // batch_size
        
        typer.echo(f"Processing batch {batch_num}/{total_batches} ({len(batch)} themes)...")
        
        user = user_theme_candidates(
            themes=batch,
            remaining_days=cfg.remaining_days,
            min_weight=cfg.min_weight,
            max_weight=cfg.max_weight,
            liquidity_dollar_min=cfg.min_avg_dollar_volume,
        )

        # Theme candidate generation can take longer due to multiple themes
        result = chat_json(client, chosen_model, system, user, timeout=300.0)  # 5 minute timeout

        try:
            parsed = CandidateResponse.model_validate(result)
            all_candidates.extend(parsed.candidates)
            typer.echo(f"  Generated {len(parsed.candidates)} candidates from batch {batch_num}")
        except Exception as e:
            typer.echo(f"Failed to parse theme candidates JSON for batch {batch_num}: {e}")
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2), encoding='utf-8')
            raise typer.Exit(code=1)

    # Merge all batches
    merged = CandidateResponse(candidates=all_candidates)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(merged.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Generated {len(merged.candidates)} total theme-based candidates -> {out}")


def main():
    app()


if __name__ == "__main__":
    main()

