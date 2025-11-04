#!/usr/bin/env python
"""Main entry point for astro-llm agent."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import typer
from agent.universe import app as universe_app
from agent.themes import app as themes_app
from agent.data_fetcher import app as data_app
from agent.scoring import app as scoring_app
from agent.analyze_recommendations import app as analyze_app

# Create main app with subcommands
main_app = typer.Typer()
main_app.add_typer(universe_app, name="universe", help="Generate universe candidates")
main_app.add_typer(themes_app, name="themes", help="Identify themes and generate theme-based candidates")
main_app.add_typer(data_app, name="data", help="Fetch price, fundamentals, analyst recs, and news")
main_app.add_typer(scoring_app, name="score", help="Score candidates with factor analysis and sentiment")
main_app.add_typer(analyze_app, name="analyze", help="Analyze analyst recommendation distribution")

if __name__ == "__main__":
    main_app()

