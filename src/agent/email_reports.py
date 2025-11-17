from __future__ import annotations

import json
import os
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import typer

from .config import load_config
from .run_manager import find_all_portfolios

app = typer.Typer()


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
    
    # S&P 500 comparison
    if sp500_comparison:
        sp500_return = sp500_comparison.get("return_pct", 0)
        outperformance = portfolio_metrics.get("outperformance", 0)
        lines.append("Benchmark Comparison:")
        lines.append(f"  S&P 500 Return: {sp500_return:+.2f}%")
        lines.append(f"  Outperformance: {outperformance:+.2f}%")
        lines.append("")
    
    # Top performers
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
    
    return "\n".join(lines)


def send_daily_report(
    email_to: str,
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
        email_to: Recipient email address
        email_from: Sender email address (defaults to email_to)
        smtp_server: SMTP server hostname
        smtp_port: SMTP server port
        smtp_user: SMTP username (if different from email_from)
        smtp_password: SMTP password or app password
        latest_portfolio_path: Path to today's portfolio file
    
    Returns:
        True if email sent successfully
    """
    cfg = load_config()
    
    # Use email_to as sender if not specified
    if email_from is None:
        email_from = email_to
    
    if smtp_user is None:
        smtp_user = email_from
    
    # Find all portfolios and their performance reports
    portfolios = find_all_portfolios()
    
    if not portfolios:
        typer.echo("[WARN] No portfolios found to report on")
        return False
    
    # Build email content
    email_body = []
    email_body.append("=" * 80)
    email_body.append("DAILY PORTFOLIO PERFORMANCE REPORT")
    email_body.append("=" * 80)
    email_body.append("")
    email_body.append(f"Report Date: {date.today().strftime('%B %d, %Y')}")
    email_body.append(f"Total Portfolios: {len(portfolios)}")
    email_body.append("")
    
    # Performance summaries for all portfolios
    email_body.append("=" * 80)
    email_body.append("ALL PORTFOLIO PERFORMANCE")
    email_body.append("=" * 80)
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
    
    # Today's new portfolio report
    if latest_portfolio_path and latest_portfolio_path.exists():
        email_body.append("")
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
    
    email_body.append("=" * 80)
    email_body.append("End of Report")
    email_body.append("=" * 80)
    
    # Create email
    msg = MIMEMultipart()
    msg['From'] = email_from
    msg['To'] = email_to
    msg['Subject'] = f"Daily Portfolio Report - {date.today().strftime('%Y-%m-%d')}"
    
    body_text = "\n".join(email_body)
    msg.attach(MIMEText(body_text, 'plain'))
    
    # Send email
    try:
        typer.echo(f"Sending email to {email_to}...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        typer.echo(f"[SUCCESS] Email sent successfully to {email_to}")
        return True
    except Exception as e:
        typer.echo(f"[ERROR] Failed to send email: {e}")
        return False


@app.command()
def send(
    email_to: Optional[str] = typer.Option(None, help="Recipient email address (defaults to EMAIL_TO env var)"),
    email_from: Optional[str] = typer.Option(None, help="Sender email address (defaults to email_to)"),
    smtp_server: str = typer.Option("smtp.gmail.com", help="SMTP server hostname"),
    smtp_port: int = typer.Option(587, help="SMTP server port"),
    smtp_user: Optional[str] = typer.Option(None, help="SMTP username (defaults to email_from)"),
    smtp_password: Optional[str] = typer.Option(None, help="SMTP password or app password (from env: SMTP_PASSWORD)"),
    latest_portfolio: Optional[Path] = typer.Option(None, help="Path to today's portfolio file"),
):
    """Send daily performance and portfolio report via email."""
    # Get email from environment if not provided
    if email_to is None:
        email_to = os.getenv("EMAIL_TO")
    
    if not email_to:
        typer.echo("[ERROR] Email recipient required. Set EMAIL_TO env var or use --email-to")
        raise typer.Exit(code=1)
    
    # Get password from environment if not provided
    if smtp_password is None:
        smtp_password = os.getenv("SMTP_PASSWORD")
    
    if not smtp_password:
        typer.echo("[ERROR] SMTP password required. Set SMTP_PASSWORD env var or use --smtp-password")
        raise typer.Exit(code=1)
    
    # Auto-detect latest portfolio if not provided
    if latest_portfolio is None:
        portfolios = find_all_portfolios()
        if portfolios:
            latest_portfolio = portfolios[-1][0]  # Most recent portfolio
    
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

