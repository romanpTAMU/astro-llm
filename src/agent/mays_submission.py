from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .models import Portfolio

app = typer.Typer()


def format_ai_query_thread(
    system_prompt: str, 
    user_prompt: str, 
    llm_response: dict,
    portfolio: Optional[Portfolio] = None
) -> str:
    """Format the AI query thread for submission.
    
    Args:
        system_prompt: System prompt sent to LLM
        user_prompt: User prompt sent to LLM
        llm_response: Full LLM response (JSON)
        portfolio: Portfolio object for generating overview
    
    Returns:
        Formatted string with prompts and responses
    """
    lines = []
    
    # Add portfolio overview if available
    if portfolio:
        lines.append("=== PORTFOLIO OVERVIEW ===")
        overview = generate_portfolio_overview(portfolio)
        lines.append(overview)
        lines.append("")
    
    lines.append("=== SYSTEM PROMPT ===")
    lines.append(system_prompt)
    lines.append("")
    lines.append("=== USER PROMPT ===")
    lines.append(user_prompt)
    lines.append("")
    lines.append("=== AI RESPONSE ===")
    lines.append(json.dumps(llm_response, indent=2))
    return "\n".join(lines)


def generate_portfolio_overview(portfolio: Portfolio) -> str:
    """Generate a paragraph overview of the portfolio.
    
    Args:
        portfolio: Portfolio object
    
    Returns:
        Formatted overview text
    """
    # Sort holdings by weight
    sorted_holdings = sorted(portfolio.holdings, key=lambda x: x.weight, reverse=True)
    
    # Get top holdings
    top_holdings = sorted_holdings[:5]
    
    # Calculate sector allocation
    sector_allocation = portfolio.sector_allocation
    top_sectors = sorted(sector_allocation.items(), key=lambda x: x[1], reverse=True)[:3] if sector_allocation else []
    
    # Count themes
    themes = [h.theme for h in portfolio.holdings if h.theme]
    unique_themes = list(set(themes)) if themes else []
    
    # Build overview
    overview_parts = []
    
    # Opening
    constructed_date = portfolio.constructed_at
    if hasattr(constructed_date, 'strftime'):
        date_str = constructed_date.strftime('%B %d, %Y')
    else:
        date_str = str(constructed_date)
    
    overview_parts.append(
        f"This portfolio consists of {len(portfolio.holdings)} carefully selected stocks "
        f"with a total allocation of {portfolio.total_weight*100:.1f}%, constructed on {date_str}."
    )
    
    # Top holdings
    if top_holdings:
        top_weights = [f"{h.ticker} ({h.weight*100:.1f}%)" for h in top_holdings]
        if len(top_weights) > 1:
            overview_parts.append(
                f"The top holdings include {', '.join(top_weights[:-1])}, and {top_weights[-1]}."
            )
        else:
            overview_parts.append(f"The top holding is {top_weights[0]}.")
    
    # Sector allocation
    if top_sectors:
        sector_text = ", ".join([f"{sector} ({weight*100:.1f}%)" for sector, weight in top_sectors])
        overview_parts.append(
            f"Sector allocation is diversified with the largest exposures in {sector_text}."
        )
    
    # Themes (if any)
    if unique_themes:
        theme_text = ", ".join([f'"{theme}"' for theme in unique_themes[:3]])
        overview_parts.append(
            f"The portfolio reflects several investment themes including {theme_text}."
        )
    
    # Strategy note
    holdings_with_scores = [h for h in portfolio.holdings if h.composite_score is not None]
    if holdings_with_scores:
        avg_score = sum(h.composite_score for h in holdings_with_scores) / len(holdings_with_scores)
        overview_parts.append(
            f"All holdings were selected based on comprehensive factor analysis, sentiment analysis, "
            f"and risk screening, with an average composite score of {avg_score:.2f}."
        )
    else:
        overview_parts.append(
            "All holdings were selected based on comprehensive factor analysis, sentiment analysis, "
            "and risk screening."
        )
    
    return " ".join(overview_parts)


def submit_portfolio(
    portfolio_file: Path,
    team_name: str = "ASTRO",
    team_leader_email: str = "romanp@tamu.edu",
    system_prompt: Optional[str] = None,
    user_prompt: Optional[str] = None,
    llm_response: Optional[dict] = None,
    headless: bool = False,
    wait_timeout: int = 30000,
) -> bool:
    """Submit portfolio to MAYS AI competition website.
    
    Args:
        portfolio_file: Path to portfolio JSON file
        team_name: Team name for login
        team_leader_email: Team leader email for login
        system_prompt: System prompt used (for AI query thread)
        user_prompt: User prompt used (for AI query thread)
        llm_response: LLM response (for AI query thread)
        headless: Run browser in headless mode
        wait_timeout: Timeout for page operations (ms)
    
    Returns:
        True if submission successful, False otherwise
    """
    # Load portfolio
    if not portfolio_file.exists():
        typer.echo(f"Portfolio file not found: {portfolio_file}")
        return False
    
    portfolio_data = json.loads(portfolio_file.read_text(encoding='utf-8'))
    try:
        portfolio = Portfolio.model_validate(portfolio_data)
    except Exception as e:
        typer.echo(f"Failed to parse portfolio file: {e}")
        return False
    
    if len(portfolio.holdings) != 20:
        typer.echo(f"Portfolio must have exactly 20 holdings, got {len(portfolio.holdings)}")
        return False
    
    # Check total weight
    total_weight = sum(h.weight for h in portfolio.holdings)
    if abs(total_weight - 1.0) > 0.01:
        typer.echo(f"[WARN] Total weight is {total_weight*100:.2f}%, expected 100%")
    
    typer.echo("=" * 80)
    typer.echo("MAYS AI COMPETITION SUBMISSION")
    typer.echo("=" * 80)
    typer.echo(f"Team: {team_name}")
    typer.echo(f"Email: {team_leader_email}")
    typer.echo(f"Portfolio: {portfolio_file}")
    typer.echo("")
    
    with sync_playwright() as p:
        typer.echo("Launching browser...")
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to login page
            typer.echo("Navigating to login page...")
            page.goto("https://joshdrobert.github.io/Mays-AI/#login", wait_until="networkidle")
            time.sleep(2)  # Wait for page to fully load
            
            # Fill login form using exact IDs from HTML
            typer.echo("Filling login form...")
            team_input = page.locator('#loginTeamName')
            email_input = page.locator('#loginEmail')
            
            team_input.fill(team_name)
            time.sleep(0.5)
            email_input.fill(team_leader_email)
            time.sleep(0.5)
            
            # Click login button
            typer.echo("Clicking login button...")
            login_form = page.locator('#loginForm')
            login_form.locator('button[type="submit"]').click()
            time.sleep(3)  # Wait for navigation
            
            # Check if we're on dashboard
            current_url = page.url
            typer.echo(f"Current URL: {current_url}")
            
            # Navigate to submission page by clicking "Submit Daily Stocks" button
            # This is necessary because the site uses client-side routing
            typer.echo("Clicking 'Submit Daily Stocks' button to navigate to submission page...")
            submit_button = page.locator('a:has-text("Submit Daily Stocks"), a[href="#submit"]').first
            submit_button.click()
            time.sleep(2)  # Wait for page transition
            
            # Wait for submission form to be visible
            page.wait_for_selector('#submitForm', state='visible', timeout=wait_timeout)
            time.sleep(1)
            
            # Fill submission form using exact IDs and classes from HTML
            typer.echo("Filling submission form...")
            
            # Select "Active Portfolio" from dropdown (value="A")
            typer.echo("  Selecting portfolio type...")
            portfolio_type_select = page.locator('#portfolioType')
            portfolio_type_select.select_option(value="A", timeout=wait_timeout)
            time.sleep(1)
            
            # Fill in stock tickers and weights
            typer.echo("  Filling stock selections...")
            
            # Wait for form to be ready
            page.wait_for_load_state("networkidle")
            time.sleep(1)
            
            # Get all stock ticker and weight inputs using the exact classes
            ticker_inputs = page.locator('.stock-ticker').all()
            weight_inputs = page.locator('.stock-weight').all()
            
            typer.echo(f"    Found {len(ticker_inputs)} ticker inputs and {len(weight_inputs)} weight inputs")
            
            if len(ticker_inputs) < 20 or len(weight_inputs) < 20:
                typer.echo(f"    [WARN] Expected 20 inputs each, got {len(ticker_inputs)} tickers and {len(weight_inputs)} weights")
            
            # Fill each stock
            for i, holding in enumerate(portfolio.holdings, 1):
                typer.echo(f"    Stock {i}/20: {holding.ticker} ({holding.weight*100:.2f}%)")
                
                if i <= len(ticker_inputs):
                    ticker_inputs[i-1].fill(holding.ticker)
                    time.sleep(0.2)
                else:
                    typer.echo(f"      [WARN] Could not find ticker input for stock {i}")
                
                if i <= len(weight_inputs):
                    weight_pct = holding.weight * 100
                    weight_inputs[i-1].fill(str(weight_pct))
                    time.sleep(0.2)
                else:
                    typer.echo(f"      [WARN] Could not find weight input for stock {i}")
            
            # Fill AI Query Thread using exact ID
            typer.echo("  Filling AI query thread...")
            if system_prompt and user_prompt and llm_response:
                query_thread = format_ai_query_thread(system_prompt, user_prompt, llm_response, portfolio)
            else:
                # Fallback: create a basic query thread
                query_thread = f"Portfolio constructed on {portfolio.constructed_at}\n\n"
                query_thread += f"Selected {len(portfolio.holdings)} stocks with total weight {portfolio.total_weight*100:.2f}%\n\n"
                query_thread += "Holdings:\n"
                for holding in portfolio.holdings:
                    query_thread += f"- {holding.ticker}: {holding.weight*100:.2f}% ({holding.sector or 'Unknown'})\n"
            
            query_textarea = page.locator('#aiQuery')
            query_textarea.fill(query_thread)
            time.sleep(0.5)
            
            # Submit form using exact form ID
            typer.echo("  Submitting portfolio...")
            submit_form = page.locator('#submitForm')
            submit_form.locator('button[type="submit"]').click()
            time.sleep(3)
            
            # Check for success message
            success_indicator = page.locator('text=/success/i, text=/submitted/i, .success, [class*="success"]')
            if success_indicator.count() > 0:
                typer.echo("")
                typer.echo("=" * 80)
                typer.echo("✓ PORTFOLIO SUBMITTED SUCCESSFULLY")
                typer.echo("=" * 80)
                return True
            else:
                typer.echo("")
                typer.echo("[WARN] Submission completed, but success message not detected")
                typer.echo("Please verify submission manually on the website")
                return True  # Assume success if no error
            
        except PlaywrightTimeoutError as e:
            typer.echo(f"[ERROR] Timeout error: {e}")
            typer.echo("The page may have taken too long to load. Try increasing wait_timeout.")
            return False
        except Exception as e:
            typer.echo(f"[ERROR] Submission failed: {e}")
            typer.echo("")
            typer.echo("Debug info:")
            typer.echo(f"  URL: {page.url}")
            typer.echo(f"  Title: {page.title()}")
            # Take screenshot for debugging
            screenshot_path = Path("submission_error.png")
            page.screenshot(path=str(screenshot_path))
            typer.echo(f"  Screenshot saved to: {screenshot_path}")
            return False
        finally:
            if not headless:
                typer.echo("")
                typer.echo("Browser will remain open for 10 seconds for manual inspection...")
                time.sleep(10)
            browser.close()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    portfolio_file: Path = typer.Option(
        Path("data/portfolio.json"), help="Portfolio JSON file to submit"
    ),
    team_name: str = typer.Option(
        "ASTRO", help="Team name for login"
    ),
    team_leader_email: str = typer.Option(
        "romanp@tamu.edu", help="Team leader email for login"
    ),
    prompts_file: Optional[Path] = typer.Option(
        None, help="JSON file containing system_prompt, user_prompt, and llm_response (auto-detected if in same folder as portfolio)"
    ),
    headless: bool = typer.Option(
        False, help="Run browser in headless mode"
    ),
):
    """Submit portfolio to MAYS AI competition website."""
    if ctx.invoked_subcommand is not None:
        return
    
    system_prompt = None
    user_prompt = None
    llm_response = None
    
    # Auto-detect prompts file if not provided
    if prompts_file is None:
        prompts_file = portfolio_file.parent / "prompts_and_response.json"
        if not prompts_file.exists():
            typer.echo(f"[INFO] Prompts file not found at {prompts_file}, will use fallback query thread")
            prompts_file = None
    
    if prompts_file and prompts_file.exists():
        prompts_data = json.loads(prompts_file.read_text(encoding='utf-8'))
        system_prompt = prompts_data.get("system_prompt")
        user_prompt = prompts_data.get("user_prompt")
        llm_response = prompts_data.get("llm_response")
        typer.echo(f"Loaded prompts and response from {prompts_file}")
    
    success = submit_portfolio(
        portfolio_file=portfolio_file,
        team_name=team_name,
        team_leader_email=team_leader_email,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        llm_response=llm_response,
        headless=headless,
    )
    
    if not success:
        raise typer.Exit(code=1)

