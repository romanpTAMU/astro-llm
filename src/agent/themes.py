from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .config import load_config
from .openai_client import get_client, chat_json
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
    model: Optional[str] = typer.Option(None, help="OpenAI model override"),
):
    """Identify major market themes."""
    cfg = load_config()
    chosen_model = model or cfg.openai_model

    client = get_client()

    system = system_themes()
    user = user_themes(cfg.portfolio_horizon_end, cfg.remaining_days)

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
    model: Optional[str] = typer.Option(None, help="OpenAI model override"),
):
    """Generate stock candidates based on identified themes."""
    cfg = load_config()
    chosen_model = model or cfg.openai_model

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
    user = user_theme_candidates(
        themes=themes_list,
        remaining_days=cfg.remaining_days,
        min_weight=cfg.min_weight,
        max_weight=cfg.max_weight,
        liquidity_dollar_min=cfg.min_avg_dollar_volume,
    )

    result = chat_json(client, chosen_model, system, user)

    try:
        parsed = CandidateResponse.model_validate(result)
    except Exception as e:
        typer.echo(f"Failed to parse theme candidates JSON: {e}")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding='utf-8')
        raise typer.Exit(code=1)

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    out.write_text(parsed.model_dump_json(indent=2), encoding='utf-8')
    typer.echo(f"Generated {len(parsed.candidates)} theme-based candidates -> {out}")


def main():
    app()


if __name__ == "__main__":
    main()

