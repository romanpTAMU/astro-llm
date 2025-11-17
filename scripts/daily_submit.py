#!/usr/bin/env python3
"""
Daily portfolio generation and reporting script.

This script runs the full pipeline:
1. Generate universe
2. Identify themes (using FMP General News)
3. Generate theme-based candidates
4. Merge candidates
5. Fetch data
6. Score candidates (with news summaries)
7. Build portfolio
8. Generate portfolio report
9. Track performance of all portfolios
10. Send email report (performance + today's portfolio)
11. Submit to MAYS AI competition (optional, default: disabled)

Designed to run daily before market open (8:30 AM CST).
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, date
from typing import Optional
import subprocess
import json

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent.run_manager import get_run_folder
from src.agent.portfolio import Portfolio
import typer


def is_trading_day(check_date: date | None = None) -> bool:
    """Check if a date is a trading day (weekday, not a major holiday).
    
    Args:
        check_date: Date to check (defaults to today)
    
    Returns:
        True if it's a trading day
    """
    if check_date is None:
        check_date = date.today()
    
    # Check if weekend
    if check_date.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False
    
    # Check for major US market holidays (simplified list)
    # You may want to use a library like pandas_market_calendars for more accuracy
    month_day = (check_date.month, check_date.day)
    
    # Major holidays (approximate - actual dates vary by year)
    holidays = [
        (1, 1),   # New Year's Day
        (7, 4),   # Independence Day
        (12, 25), # Christmas
    ]
    
    # Check if it's a holiday
    if month_day in holidays:
        return False
    
    # Check for Monday holidays (MLK Day, Presidents Day, Memorial Day, Labor Day, Thanksgiving)
    # These are more complex and vary by year, so we'll use a simple heuristic
    # For production, consider using pandas_market_calendars
    
    return True


def run_command(cmd: list[str], description: str) -> bool:
    """Run a command and return success status.
    
    Args:
        cmd: Command to run as list
        description: Description for logging
    
    Returns:
        True if successful
    """
    print(f"\n{'='*80}")
    print(f"STEP: {description}")
    print(f"{'='*80}")
    print(f"Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=False,
            text=True
        )
        print(f"[SUCCESS] {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {description} failed with exit code {e.returncode}")
        return False
    except Exception as e:
        print(f"[ERROR] {description} failed with error: {e}")
        return False


def main(
    skip_submission: bool = True,  # Default to not submitting automatically
    force: bool = False,
    portfolio_file: Optional[str] = None,
    send_email: bool = True,  # Default to sending email reports
    email_to: Optional[str] = None,
):
    """Run the daily portfolio generation and submission pipeline.
    
    Args:
        skip_submission: Skip the submission step (for testing)
        force: Run even if not a trading day
        portfolio_file: Use existing portfolio file instead of generating new one
    """
    print("="*80)
    print("DAILY PORTFOLIO GENERATION & SUBMISSION")
    print("="*80)
    print(f"Date: {date.today()}")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print()
    
    # Check if it's a trading day
    if not force and not is_trading_day():
        print("[WARN] Today is not a trading day. Use --force to run anyway.")
        return 1
    
    # Determine Python executable
    python_exe = sys.executable
    base_dir = Path(__file__).parent.parent
    
    # If portfolio_file is provided, skip generation and go straight to submission
    if portfolio_file:
        portfolio_path = Path(portfolio_file)
        if not portfolio_path.exists():
            print(f"[ERROR] Portfolio file not found: {portfolio_path}")
            return 1
        
        print(f"Using existing portfolio: {portfolio_path}")
        
        if not skip_submission:
            print("\nSubmitting portfolio...")
            try:
                from src.agent.mays_submission import submit_portfolio
                success = submit_portfolio(
                    portfolio_file=portfolio_path,
                    headless=True,
                )
                if success:
                    print("[SUCCESS] Portfolio submitted successfully")
                    return 0
                else:
                    print("[ERROR] Portfolio submission failed")
                    return 1
            except Exception as e:
                print(f"[ERROR] Error during submission: {e}")
                return 1
        return 0
    
    # Step 1: Generate universe
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "universe", "generate"],
        "Generate Universe"
    ):
        return 1
    
    # Step 1.5: Identify themes using recent news
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "themes", "identify"],
        "Identify Market Themes"
    ):
        print("[WARN] Theme identification failed, continuing without themes...")
        # Continue without themes - will use regular candidates only
    
    # Step 1.6: Generate theme-based candidates (only if themes were identified)
    theme_candidates_file = base_dir / "data" / "theme_candidates.json"
    if (base_dir / "data" / "themes.json").exists():
        if not run_command(
            [python_exe, str(base_dir / "main.py"), "themes", "generate-candidates"],
            "Generate Theme-Based Candidates"
        ):
            print("[WARN] Theme candidate generation failed, continuing without theme candidates...")
    
    # Step 1.7: Merge regular and theme candidates (if theme candidates exist)
    if theme_candidates_file.exists():
        if not run_command(
            [python_exe, str(base_dir / "main.py"), "universe", "merge"],
            "Merge Candidates"
        ):
            print("[WARN] Candidate merge failed, using regular candidates only...")
            # Fall back to using regular candidates
            import shutil
            merged_file = base_dir / "data" / "merged_candidates.json"
            regular_file = base_dir / "data" / "candidates.json"
            if regular_file.exists() and not merged_file.exists():
                shutil.copy2(regular_file, merged_file)
                print(f"  Copied {regular_file.name} to {merged_file.name}")
    else:
        # No theme candidates, just use regular candidates
        import shutil
        merged_file = base_dir / "data" / "merged_candidates.json"
        regular_file = base_dir / "data" / "candidates.json"
        if regular_file.exists() and not merged_file.exists():
            shutil.copy2(regular_file, merged_file)
            print(f"  Using regular candidates only (no theme candidates found)")
    
    # Step 2: Fetch data (use merged candidates)
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "data", "fetch"],
        "Fetch Stock Data"
    ):
        return 1
    
    # Step 3: Score candidates
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "score", "score"],
        "Score Candidates"
    ):
        return 1
    
    # Step 4: Build portfolio
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "portfolio", "build"],
        "Build Portfolio"
    ):
        return 1
    
    # Find the portfolio file in the latest run folder
    runs_dir = base_dir / "data" / "runs"
    if not runs_dir.exists():
        print("[ERROR] No run folders found")
        return 1
    
    # Get the most recent run folder
    run_folders = sorted([d for d in runs_dir.iterdir() if d.is_dir()], reverse=True)
    if not run_folders:
        print("[ERROR] No run folders found")
        return 1
    
    latest_run = run_folders[0]
    portfolio_file = latest_run / "portfolio.json"
    
    if not portfolio_file.exists():
        print(f"[ERROR] Portfolio file not found: {portfolio_file}")
        return 1
    
    print(f"\n[SUCCESS] Portfolio created: {portfolio_file}")
    
    # Step 5: Generate portfolio report
    print("\n" + "="*80)
    print("GENERATING PORTFOLIO REPORT")
    print("="*80)
    report_file = latest_run / "portfolio_report.md"
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "report", "generate", 
         "--portfolio-file", str(portfolio_file), "--out", str(report_file)],
        "Generate Portfolio Report"
    ):
        print("[WARN] Portfolio report generation failed, continuing...")
    
    # Step 6: Track performance of all portfolios
    print("\n" + "="*80)
    print("TRACKING PORTFOLIO PERFORMANCE")
    print("="*80)
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "performance", "track"],
        "Track All Portfolio Performance"
    ):
        print("[WARN] Performance tracking failed, continuing...")
    
    # Step 7: Send email report
    if send_email:
        print("\n" + "="*80)
        print("SENDING EMAIL REPORT")
        print("="*80)
        import os
        smtp_password = os.getenv("SMTP_PASSWORD")
        if not smtp_password:
            print("[WARN] SMTP_PASSWORD not set, skipping email report")
            print("  Set SMTP_PASSWORD environment variable to enable email reports")
        else:
            recipient = email_to or os.getenv("EMAIL_TO", "romanp@tamu.edu")
            # Get sender email (defaults to recipient if not set)
            sender = os.getenv("EMAIL_FROM", recipient)
            email_cmd = [
                python_exe, str(base_dir / "main.py"), "email", "send",
                "--email-to", recipient,
                "--email-from", sender,
                "--latest-portfolio", str(portfolio_file),
            ]
            if not run_command(email_cmd, "Send Email Report"):
                print("[WARN] Email sending failed, continuing...")
    
    # Step 8: Submit portfolio (optional)
    if not skip_submission:
        print("\n" + "="*80)
        print("SUBMITTING PORTFOLIO")
        print("="*80)
        try:
            from src.agent.mays_submission import submit_portfolio
            success = submit_portfolio(
                portfolio_file=portfolio_file,
                headless=True,
            )
            if success:
                print("[SUCCESS] Portfolio submitted successfully")
                return 0
            else:
                print("[ERROR] Portfolio submission failed")
                return 1
        except Exception as e:
            print(f"[ERROR] Error during submission: {e}")
            import traceback
            traceback.print_exc()
            return 1
    else:
        print("\n[INFO] Submission skipped (--skip-submission flag)")
        return 0


if __name__ == "__main__":
    typer.run(main)

