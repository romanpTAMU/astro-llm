from __future__ import annotations

import json
import os
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional, Union

import typer

from .config import load_config
from .run_manager import find_all_portfolios

app = typer.Typer()

# Default email recipients list
DEFAULT_EMAIL_RECIPIENTS = [
    "romanp@tamu.edu",
    "jamdadam@tamu.edu",
    "brent.adams@tamu.edu",
    "nthnbrks@tamu.edu",
    "ketanverma123@tamu.edu",
    "shameel10@tamu.edu",
    "zacharyr@tamu.edu",
]


def format_performance_summary(perf_report_path: Path) -> Optional[str]:
    """Format a performance report JSON into a readable text summary.

    Args:
        perf_report_path: Path to performance_report.json

    Returns:
        Formatted text summary or None if file doesn't exist
    """
    if not perf_report_path.exists():
        return None

    try:
        report_data = json.loads(perf_report_path.read_text(encoding='utf-8'))
    except Exception:
        return None

    portfolio_metrics = report_data.get("portfolio_metrics", {})
    sp500_comparison = report_data.get("sp500_comparison")

    lines = []
    lines.append(f"Portfolio: {perf_report_path.parent.name}")
    lines.append(f"Construction Date: {report_data.get('construction_date', 'N/A')}")
    lines.append(f"Days Held: {report_data.get('days_held', 0)}")
    lines.append("")

    # Portfolio metrics
    lines.append("Performance Metrics:")
    lines.append(f"  Weighted Return: {portfolio_metrics.get('weighted_return', 0):+.2f}%")
    lines.append(f"  Average Return: {portfolio_metrics.get('simple_avg_return', 0):+.2f}%")
    lines.append(f"  Winners: {portfolio_metrics.get('winners_count', 0)}/{portfolio_metrics.get('total_holdings', 0)}")
    lines.append(f"  Losers: {portfolio_metrics.get('losers_count', 0)}/{portfolio_metrics.get('total_holdings', 0)}")
    lines.append("")

    # S&P 500 comparison and risk metrics
    if sp500_comparison:
        sp500_return = sp500_comparison.get("return_pct", 0)
        outperformance = portfolio_metrics.get("outperformance", 0)
        lines.append("Benchmark Comparison:")
        lines.append(f"  S&P 500 Return: {sp500_return:+.2f}%")
        lines.append(f"  Outperformance: {outperformance:+.2f}%")

        # Portfolio beta and alpha from stored calculations
        portfolio_beta = portfolio_metrics.get("portfolio_beta")
        portfolio_alpha = portfolio_metrics.get("portfolio_alpha")

        # If beta/alpha not available, try to fetch beta data and recalculate
        if portfolio_beta is None or portfolio_alpha is None:
            typer.echo(f"[INFO] Missing beta/alpha for {perf_report_path.parent.name}, fetching fresh beta data...")
            fresh_beta, fresh_alpha = fetch_portfolio_beta_alpha(report_data)
            if fresh_beta is not None:
                portfolio_beta = fresh_beta
                lines.append(f"  Portfolio Beta: {portfolio_beta:.2f}")
            else:
                lines.append("  Portfolio Beta: N/A (insufficient beta data)")

            if fresh_alpha is not None:
                portfolio_alpha = fresh_alpha
                lines.append(f"  Portfolio Alpha: {portfolio_alpha:+.2f}%")
            else:
                lines.append("  Portfolio Alpha: N/A (requires beta and market data)")
        else:
            lines.append(f"  Portfolio Beta: {portfolio_beta:.2f}")
            lines.append(f"  Portfolio Alpha: {portfolio_alpha:+.2f}%")

        lines.append("")

    # Top and bottom performers
    holdings = report_data.get("holdings", [])
    if holdings:
        sorted_holdings = sorted(
            [h for h in holdings if h.get("return_pct") is not None],
            key=lambda x: x.get("return_pct", 0),
            reverse=True
        )
        if sorted_holdings:
            lines.append("Top 3 Performers:")
            for i, h in enumerate(sorted_holdings[:3], 1):
                lines.append(f"  {i}. {h.get('ticker')}: {h.get('return_pct', 0):+.2f}%")

            lines.append("")
            lines.append("Bottom 3 Performers:")
            for i, h in enumerate(sorted_holdings[-3:], 1):
                lines.append(f"  {i}. {h.get('ticker')}: {h.get('return_pct', 0):+.2f}%")
            lines.append("")

    return "\n".join(lines)


def fetch_portfolio_beta_alpha(report_data: dict) -> tuple[Optional[float], Optional[float]]:
    """Fetch fresh beta data for portfolio holdings and recalculate beta/alpha.

    Args:
        report_data: The performance report JSON data

    Returns:
        Tuple of (portfolio_beta, portfolio_alpha)
    """
    from .data_fetcher import fetch_price_data
    from .performance_tracker import calculate_portfolio_beta, calculate_portfolio_alpha
    from .config import load_config

    cfg = load_config()
    holdings = report_data.get("holdings", [])
    portfolio_return = report_data.get("portfolio_metrics", {}).get("weighted_return")
    sp500_return = report_data.get("sp500_comparison", {}).get("return_pct")

    if not holdings or portfolio_return is None:
        return None, None

    # Fetch fresh beta data for each holding
    fresh_performance_data = []
    for holding in holdings:
        ticker = holding.get("ticker")
        weight = holding.get("weight", 0)

        if ticker and weight > 0:
            # Fetch fresh price data (now prioritizes FMP which has beta)
            price_data = fetch_price_data(ticker, cfg.finnhub_api_key, cfg.fmp_api_key)
            beta = price_data.beta if price_data else None

            fresh_performance_data.append({
                "ticker": ticker,
                "weight": weight,
                "beta": beta,
                "return_pct": holding.get("return_pct"),  # Keep original return
            })

    # Calculate fresh portfolio beta
    portfolio_beta = calculate_portfolio_beta(fresh_performance_data)

    # Calculate fresh portfolio alpha
    portfolio_alpha = None
    if portfolio_beta is not None and sp500_return is not None:
        portfolio_alpha = calculate_portfolio_alpha(portfolio_return, portfolio_beta, sp500_return)

    return portfolio_beta, portfolio_alpha


def build_email_content(email_to: Union[str, list[str]], latest_portfolio_path: Optional[Path] = None) -> list[str]:
    """Build email content for daily portfolio report.

    Args:
        email_to: Recipient email address(es)
        latest_portfolio_path: Path to today's portfolio file

    Returns:
        List of strings representing email body lines
    """
    cfg = load_config()

    # Normalize email_to to a list
    if isinstance(email_to, str):
        # Handle comma-separated string
        email_recipients = [e.strip() for e in email_to.split(",") if e.strip()]
    else:
        email_recipients = email_to

    if not email_recipients:
        email_recipients = DEFAULT_EMAIL_RECIPIENTS.copy()

    # Find all portfolios and their performance reports
    portfolios = find_all_portfolios()

    if not portfolios:
        return ["[WARN] No portfolios found to report on"]

    # Build email content
    email_body = []
    email_body.append("=" * 80)
    email_body.append("DAILY PORTFOLIO PERFORMANCE REPORT")
    email_body.append("=" * 80)
    email_body.append("")
    email_body.append(f"Report Date: {date.today().strftime('%B %d, %Y')}")
    email_body.append(f"Total Portfolios: {len(portfolios)}")
    email_body.append("")

    # Today's new portfolio report (put at top)
    if latest_portfolio_path and latest_portfolio_path.exists():
        email_body.append("=" * 80)
        email_body.append("TODAY'S NEW PORTFOLIO")
        email_body.append("=" * 80)
        email_body.append("")

        try:
            portfolio_data = json.loads(latest_portfolio_path.read_text(encoding='utf-8'))
            portfolio = portfolio_data

            email_body.append(f"Portfolio Date: {portfolio.get('portfolio_date', 'N/A')}")
            email_body.append(f"Total Holdings: {len(portfolio.get('holdings', []))}")
            email_body.append(f"Total Weight: {portfolio.get('total_weight', 0)*100:.2f}%")
            email_body.append("")

            # All holdings with rationale
            holdings = sorted(
                portfolio.get('holdings', []),
                key=lambda x: x.get('weight', 0),
                reverse=True
            )
            email_body.append("All Holdings (sorted by weight):")
            email_body.append("")
            for i, h in enumerate(holdings, 1):
                weight = h.get('weight', 0) * 100
                ticker = h.get('ticker', 'N/A')
                sector = h.get('sector', 'Unknown')
                theme = h.get('theme', 'N/A')
                rationale = h.get('rationale', 'No rationale provided')
                composite_score = h.get('composite_score')

                email_body.append(f"{i}. {ticker} - {weight:.2f}%")
                email_body.append(f"   Sector: {sector}")
                if theme != 'N/A':
                    email_body.append(f"   Theme: {theme}")
                if composite_score is not None:
                    email_body.append(f"   Score: {composite_score:.3f}")
                email_body.append(f"   Rationale: {rationale}")
                email_body.append("")

            # Sector allocation
            sector_allocation = portfolio.get('sector_allocation', {})
            if sector_allocation:
                email_body.append("Sector Allocation:")
                for sector, weight in sorted(sector_allocation.items(), key=lambda x: x[1], reverse=True):
                    email_body.append(f"  {sector}: {weight*100:.2f}%")
                email_body.append("")
        except Exception as e:
            email_body.append(f"Error reading portfolio: {e}")
            email_body.append("")

    # Performance summaries for all portfolios
    email_body.append("=" * 80)
    email_body.append("ALL PORTFOLIO PERFORMANCE")
    email_body.append("=" * 80)
    email_body.append("")

    # Calculate aggregate statistics across portfolios from Dec 20 onwards (post-bug fix)
    from datetime import datetime
    cutoff_date = datetime(2025, 12, 20).date()

    total_trading_days = 0
    weighted_beta_sum = 0
    weighted_alpha_sum = 0
    total_return_weighted = 0
    total_market_return_weighted = 0
    portfolio_count = 0

    for port_path, constructed_at in portfolios:
        run_folder = port_path.parent
        perf_report_path = run_folder / "performance_report.json"

        if perf_report_path.exists():
            try:
                report_data = json.loads(perf_report_path.read_text(encoding='utf-8'))
                portfolio_metrics = report_data.get("portfolio_metrics", {})
                days_held = report_data.get("days_held", 0)
                construction_date_str = report_data.get("construction_date")

                # Skip portfolios constructed before Dec 20 (buggy beta/alpha calculations)
                if construction_date_str:
                    try:
                        construction_date = datetime.fromisoformat(construction_date_str).date()
                        if construction_date < cutoff_date:
                            continue  # Skip this portfolio from aggregate calculations
                    except (ValueError, TypeError):
                        pass  # If date parsing fails, include the portfolio

                beta = portfolio_metrics.get("portfolio_beta")
                alpha = portfolio_metrics.get("portfolio_alpha")
                portfolio_return = portfolio_metrics.get("weighted_return")
                sp500_return = portfolio_metrics.get("sp500_return")

                if beta is not None and days_held > 0:
                    weighted_beta_sum += beta * days_held
                    total_trading_days += days_held
                    portfolio_count += 1

                if alpha is not None and days_held > 0:
                    weighted_alpha_sum += alpha * days_held

                if portfolio_return is not None and days_held > 0:
                    total_return_weighted += portfolio_return * days_held

                if sp500_return is not None and days_held > 0:
                    total_market_return_weighted += sp500_return * days_held

            except Exception:
                pass

    # Add aggregate performance summary
    if total_trading_days > 0 and portfolio_count > 0:
        avg_beta = weighted_beta_sum / total_trading_days
        avg_alpha = weighted_alpha_sum / total_trading_days
        avg_portfolio_return = total_return_weighted / total_trading_days
        avg_market_return = total_market_return_weighted / total_trading_days
        avg_outperformance = avg_portfolio_return - avg_market_return

        email_body.append("=" * 80)
        email_body.append("AGGREGATE PERFORMANCE SUMMARY")
        email_body.append("=" * 80)
        email_body.append("")
        email_body.append(f"Analysis across {portfolio_count} portfolios over {total_trading_days} total trading days")
        email_body.append("")
        email_body.append("Trading-Days Weighted Averages:")
        email_body.append(f"  Average Portfolio Beta: {avg_beta:.2f}")
        email_body.append(f"  Average Portfolio Alpha: {avg_alpha:+.2f}%")
        email_body.append(f"  Average Portfolio Return: {avg_portfolio_return:+.2f}%")
        email_body.append(f"  Average Market Return: {avg_market_return:+.2f}%")
        email_body.append(f"  Average Outperformance: {avg_outperformance:+.2f}%")
        email_body.append("")

    for port_path, constructed_at in portfolios:
        run_folder = port_path.parent
        perf_report_path = run_folder / "performance_report.json"

        summary = format_performance_summary(perf_report_path)
        if summary:
            email_body.append(summary)
            email_body.append("-" * 80)
            email_body.append("")
        else:
            email_body.append(f"Portfolio: {run_folder.name}")
            email_body.append("  Performance data not yet available")
            email_body.append("")

    email_body.append("=" * 80)
    email_body.append("End of Report")
    email_body.append("=" * 80)

    return email_body


def send_daily_report(
    email_to: Union[str, list[str]],
    email_from: Optional[str] = None,
    smtp_server: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
    latest_portfolio_path: Optional[Path] = None,
) -> bool:
    """Send daily performance and portfolio report via email.
    
    Note: TAMU SSO Gmail works the same as regular Gmail for SMTP.
    Use an App Password (not your regular password) for authentication.
    
    Args:
        email_to: Recipient email address(es) - can be a single string, comma-separated string, or list
        email_from: Sender email address (defaults to first email_to)
        smtp_server: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username (if different from email_from)
        smtp_password: SMTP password or app password
        latest_portfolio_path: Path to today's portfolio file
    
    Returns:
        True if email sent successfully to all recipients
    """
    # Normalize email_to to a list
    if isinstance(email_to, str):
        # Handle comma-separated string
        email_recipients = [e.strip() for e in email_to.split(",") if e.strip()]
    else:
        email_recipients = email_to

    if not email_recipients:
        typer.echo("[ERROR] No email recipients provided")
        return False

    # Use first recipient as sender if not specified
    if email_from is None:
        email_from = email_recipients[0]

    if smtp_user is None:
        smtp_user = email_from

    # Build email content
    email_body = build_email_content(email_to, latest_portfolio_path)
    
    # Create email
    msg = MIMEMultipart()
    msg['From'] = email_from
    msg['To'] = ", ".join(email_recipients)  # For display purposes
    msg['Subject'] = f"Daily Portfolio Report - {date.today().strftime('%Y-%m-%d')}"
    
    body_text = "\n".join(email_body)
    msg.attach(MIMEText(body_text, 'plain'))
    
    # Send email to all recipients
    try:
        typer.echo(f"Sending email to {len(email_recipients)} recipient(s): {', '.join(email_recipients)}...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        # Use sendmail to send to multiple recipients
        server.sendmail(email_from, email_recipients, msg.as_string())
        server.quit()
        typer.echo(f"[SUCCESS] Email sent successfully to {len(email_recipients)} recipient(s)")
        return True
    except Exception as e:
        typer.echo(f"[ERROR] Failed to send email: {e}")
        return False


@app.command()
def send(
    email_to: Optional[str] = typer.Option(None, help="Recipient email address(es) - comma-separated for multiple (defaults to EMAIL_TO env var)"),
    email_from: Optional[str] = typer.Option(None, help="Sender email address (defaults to first email_to)"),
    smtp_server: str = typer.Option("smtp.gmail.com", help="SMTP server hostname"),
    smtp_port: int = typer.Option(587, help="SMTP server port"),
    smtp_user: Optional[str] = typer.Option(None, help="SMTP username (defaults to email_from)"),
    smtp_password: Optional[str] = typer.Option(None, help="SMTP password or app password (from env: SMTP_PASSWORD)"),
    latest_portfolio: Optional[Path] = typer.Option(None, help="Path to today's portfolio file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview email content without sending"),
):
    """Send daily performance and portfolio report via email.

    Supports multiple recipients via comma-separated email addresses or EMAIL_TO env var.
    If EMAIL_TO is not set, uses default recipient list.
    """
    # Get email from environment if not provided
    if email_to is None:
        email_to = os.getenv("EMAIL_TO")
    
    # If still not set, use default list
    if not email_to:
        email_to = ",".join(DEFAULT_EMAIL_RECIPIENTS)
        typer.echo(f"[INFO] Using default email recipients: {email_to}")
    
    # Get password from environment if not provided
    if smtp_password is None:
        smtp_password = os.getenv("SMTP_PASSWORD")

    if not dry_run and not smtp_password:
        typer.echo("[ERROR] SMTP password required. Set SMTP_PASSWORD env var or use --smtp-password")
        raise typer.Exit(code=1)
    
    # Auto-detect latest portfolio if not provided
    if latest_portfolio is None:
        portfolios = find_all_portfolios()
        if portfolios:
            latest_portfolio = portfolios[-1][0]  # Most recent portfolio
    
    if dry_run:
        # Preview mode - build email content but don't send
        email_body = build_email_content(email_to, latest_portfolio)
        typer.echo("=" * 80)
        typer.echo("EMAIL PREVIEW (DRY RUN)")
        typer.echo("=" * 80)
        typer.echo("\n".join(email_body))
        typer.echo("=" * 80)
        return True
    else:
        send_daily_report(
            email_to=email_to,
            email_from=email_from,
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            latest_portfolio_path=latest_portfolio,
        )


if __name__ == "__main__":
    app()

