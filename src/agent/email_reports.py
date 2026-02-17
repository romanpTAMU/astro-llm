from __future__ import annotations

import json
import os
import smtplib
from datetime import date, datetime
from email import encoders
from email.mime.base import MIMEBase
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


def build_biweekly_email_content(run_folder: Path) -> tuple[list[str], Optional[bytes], str]:
    """Build email body for biweekly report: trades CSV summary, P&L, beta/alpha, current portfolio.

    Returns:
        (body_lines, csv_bytes for attachment or None, suggested_csv_filename)
    """
    body = []
    body.append("=" * 80)
    body.append("BIWEEKLY PORTFOLIO REPORT")
    body.append("=" * 80)
    body.append("")
    body.append(f"Report Date: {date.today().strftime('%B %d, %Y')}")
    body.append(f"Run: {run_folder.name}")
    body.append("")

    csv_bytes: Optional[bytes] = None
    csv_path = run_folder / "trades.csv"
    if csv_path.exists():
        csv_bytes = csv_path.read_bytes()
        body.append("=" * 80)
        body.append("TRADES (BUYS / SELLS)")
        body.append("=" * 80)
        body.append("")
        try:
            csv_text = csv_path.read_text(encoding="utf-8")
            for line in csv_text.strip().splitlines():
                body.append(line)
        except Exception:
            body.append("(See attached trades.csv)")
        body.append("")
    else:
        body.append("(No trades.csv for this run)")
        body.append("")

    # P&L this period and total
    period_pnl_path = run_folder / "period_pnl.json"
    pnl_ledger_path = run_folder.parent / "pnl_ledger.json"
    period_pnl: Optional[float] = None
    cumulative_pnl: Optional[float] = None
    if period_pnl_path.exists():
        try:
            data = json.loads(period_pnl_path.read_text(encoding="utf-8"))
            period_pnl = data.get("period_pnl")
        except Exception:
            pass
    if pnl_ledger_path.exists():
        try:
            ledger = json.loads(pnl_ledger_path.read_text(encoding="utf-8"))
            cumulative_pnl = ledger.get("cumulative_pnl", 0.0)
        except Exception:
            pass

    body.append("=" * 80)
    body.append("P&L")
    body.append("=" * 80)
    body.append("")
    if period_pnl is not None:
        body.append(f"  P&L this biweekly period:  ${period_pnl:+,.2f}")
    else:
        body.append("  P&L this biweekly period:  N/A (initial run or missing period_pnl.json)")
    if cumulative_pnl is not None:
        body.append(f"  Total P&L since inception: ${cumulative_pnl:+,.2f}")
    else:
        body.append("  Total P&L since inception: N/A")
    body.append("")

    # Beta and Alpha (current portfolio from performance report)
    perf_path = run_folder / "performance_report.json"
    beta_val: Optional[float] = None
    alpha_val: Optional[float] = None
    if perf_path.exists():
        try:
            report = json.loads(perf_path.read_text(encoding="utf-8"))
            metrics = report.get("portfolio_metrics", {})
            beta_val = metrics.get("portfolio_beta")
            alpha_val = metrics.get("portfolio_alpha")
        except Exception:
            pass

    body.append("=" * 80)
    body.append("RISK (CURRENT PORTFOLIO)")
    body.append("=" * 80)
    body.append("")
    body.append(f"  Beta:  {beta_val if beta_val is not None else 'N/A'}")
    body.append(f"  Alpha: {f'{alpha_val:+.2f}%' if alpha_val is not None else 'N/A'} (vs S&P 500, since construction)")
    body.append("")

    # Current portfolio: stocks, weights, reasons
    portfolio_path = run_folder / "portfolio.json"
    if portfolio_path.exists():
        body.append("=" * 80)
        body.append("CURRENT PORTFOLIO — HOLDINGS, WEIGHTS & REASONS")
        body.append("=" * 80)
        body.append("")
        try:
            portfolio_data = json.loads(portfolio_path.read_text(encoding="utf-8"))
            holdings = portfolio_data.get("holdings", [])
            holdings = sorted(holdings, key=lambda h: h.get("weight", 0), reverse=True)
            for i, h in enumerate(holdings, 1):
                ticker = h.get("ticker", "N/A")
                weight = h.get("weight", 0) * 100
                rationale = h.get("rationale", "No rationale provided")
                sector = h.get("sector", "—")
                body.append(f"{i}. {ticker}  —  {weight:.2f}%")
                body.append(f"   Sector: {sector}")
                body.append(f"   Rationale: {rationale}")
                body.append("")
        except Exception as e:
            body.append(f"Error reading portfolio: {e}")
            body.append("")
    else:
        body.append("(No portfolio.json in run folder)")
        body.append("")

    body.append("=" * 80)
    body.append("End of Biweekly Report")
    body.append("=" * 80)

    filename = f"trades_{run_folder.name}.csv"
    return body, csv_bytes, filename


def send_biweekly_report(
    email_to: Union[str, list[str]],
    run_folder: Path,
    email_from: Optional[str] = None,
    smtp_server: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None,
) -> bool:
    """Send biweekly report email (trades, P&L, beta/alpha, current portfolio). Attaches trades CSV."""
    if isinstance(email_to, str):
        email_recipients = [e.strip() for e in email_to.split(",") if e.strip()]
    else:
        email_recipients = email_to
    if not email_recipients:
        typer.echo("[ERROR] No email recipients provided")
        return False
    if email_from is None:
        email_from = email_recipients[0]
    if smtp_user is None:
        smtp_user = email_from

    body_lines, csv_bytes, csv_filename = build_biweekly_email_content(run_folder)
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = ", ".join(email_recipients)
    msg["Subject"] = f"Biweekly Portfolio Report — {date.today().strftime('%Y-%m-%d')} ({run_folder.name})"

    msg.attach(MIMEText("\n".join(body_lines), "plain"))

    if csv_bytes:
        part = MIMEBase("text", "csv")
        part.set_payload(csv_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=csv_filename)
        msg.attach(part)

    try:
        typer.echo(f"Sending biweekly email to {len(email_recipients)} recipient(s)...")
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, email_recipients, msg.as_string())
        server.quit()
        typer.echo(f"[SUCCESS] Biweekly email sent to {len(email_recipients)} recipient(s)")
        return True
    except Exception as e:
        typer.echo(f"[ERROR] Failed to send biweekly email: {e}")
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


@app.command("send-biweekly")
def send_biweekly_cmd(
    run_folder: Optional[Path] = typer.Option(
        None,
        "--run-folder",
        help="Biweekly run folder (e.g. data/runs_biweekly/2026-01-26_08-00-00). Default: infer from --portfolio-file.",
    ),
    portfolio_file: Optional[Path] = typer.Option(
        None,
        "--portfolio-file",
        help="Path to portfolio.json; run folder is its parent (used if --run-folder not set).",
    ),
    email_to: Optional[str] = typer.Option(
        None,
        help="Recipient(s), comma-separated. Default: BIWEEKLY_EMAIL_TO env.",
    ),
    email_from: Optional[str] = typer.Option(None, help="Sender. Default: first email_to or BIWEEKLY_EMAIL_FROM."),
    smtp_server: str = typer.Option("smtp.gmail.com", help="SMTP server"),
    smtp_port: int = typer.Option(587, help="SMTP port"),
    smtp_password: Optional[str] = typer.Option(None, help="SMTP password; default SMTP_PASSWORD env."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print email content, do not send."),
):
    """Send biweekly report: trades CSV (attached + in body), period P&L, total P&L, beta, alpha, current portfolio."""
    if run_folder is None and portfolio_file is not None and portfolio_file.exists():
        run_folder = portfolio_file.parent
    if run_folder is None or not run_folder.exists():
        typer.echo("[ERROR] Need --run-folder or --portfolio-file pointing to a biweekly run.")
        raise typer.Exit(code=1)
    if email_to is None:
        email_to = os.getenv("BIWEEKLY_EMAIL_TO")
    if not email_to or not email_to.strip():
        typer.echo("[ERROR] Set BIWEEKLY_EMAIL_TO or pass --email-to for biweekly report.")
        raise typer.Exit(code=1)
    if not dry_run:
        pw = smtp_password or os.getenv("SMTP_PASSWORD")
        if not pw:
            typer.echo("[ERROR] SMTP_PASSWORD required. Set env or use --smtp-password")
            raise typer.Exit(code=1)
    body_lines, _, _ = build_biweekly_email_content(run_folder)
    if dry_run:
        typer.echo("=" * 80)
        typer.echo("BIWEEKLY EMAIL PREVIEW (DRY RUN)")
        typer.echo("=" * 80)
        typer.echo("\n".join(body_lines))
        return
    email_from_val = email_from or os.getenv("BIWEEKLY_EMAIL_FROM", "").strip() or email_to.split(",")[0].strip()
    send_biweekly_report(
        email_to=email_to.strip(),
        run_folder=run_folder,
        email_from=email_from_val,
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        smtp_password=smtp_password or os.getenv("SMTP_PASSWORD"),
    )


if __name__ == "__main__":
    app()

