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
from agent.portfolio import app as portfolio_app
from agent.momentum_analysis import app as momentum_app
from agent.portfolio_report import app as report_app
from agent.performance_tracker import app as performance_app
from agent.email_reports import app as email_app

# Try to import submission app (requires playwright)
try:
    from agent.mays_submission import app as submission_app
    HAS_SUBMISSION = True
except ImportError:
    HAS_SUBMISSION = False
    submission_app = None

# Create main app with subcommands
main_app = typer.Typer()
main_app.add_typer(universe_app, name="universe", help="Generate universe candidates")
main_app.add_typer(themes_app, name="themes", help="Identify themes and generate theme-based candidates")
main_app.add_typer(data_app, name="data", help="Fetch price, fundamentals, analyst recs, and news")
main_app.add_typer(scoring_app, name="score", help="Score candidates with factor analysis and sentiment")
main_app.add_typer(analyze_app, name="analyze", help="Analyze analyst recommendation distribution")
main_app.add_typer(portfolio_app, name="portfolio", help="Construct final portfolio from scored candidates")
main_app.add_typer(momentum_app, name="momentum", help="Analyze portfolio momentum tilt")
main_app.add_typer(report_app, name="report", help="Generate human-readable portfolio report")
main_app.add_typer(performance_app, name="performance", help="Track portfolio performance since construction")
if HAS_SUBMISSION:
    main_app.add_typer(submission_app, name="submit", help="Submit portfolio to MAYS AI competition")
main_app.add_typer(email_app, name="email", help="Send email reports")

if __name__ == "__main__":
    main_app()

