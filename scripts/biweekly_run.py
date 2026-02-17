#!/usr/bin/env python3
"""
Bi-weekly portfolio run (Sundays, every 2 weeks).

Runs the full pipeline like daily_submit but:
- Saves to data/runs_biweekly/ (separate from daily runs)
- Tracks performance only for biweekly runs (--runs-dir data/runs_biweekly)
- Does NOT submit to MAYS AI
- Does NOT send email
- Writes a trades CSV (B/S, SYMBOL, QTY, PRICE, PRINCIPAL) in the run folder

Schedule this script every 2 weeks on Sunday (e.g. via Task Scheduler).
"""

from __future__ import annotations

import json
import os
import sys
import shutil
from pathlib import Path
from datetime import datetime, date
from typing import Optional

import subprocess
from dotenv import load_dotenv

load_dotenv()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import typer

BIWEEKLY_RUNS_DIR = Path("data/runs_biweekly")


def is_biweekly_day(check_date: date | None = None) -> bool:
    """
    Return True on the day we expect the scheduled biweekly task to run.

    NOTE: This must stay in sync with scripts/schedule_biweekly.ps1
    (DaysOfWeek / time). Currently we run on Mondays at 8:00 AM, so the
    check here is for weekday() == 0 (Monday).
    """
    if check_date is None:
        check_date = date.today()
    # Monday == 0, Sunday == 6
    return check_date.weekday() == 0


def run_command(cmd: list[str], description: str) -> bool:
    print(f"\n{'='*80}")
    print(f"STEP: {description}")
    print(f"{'='*80}")
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=False,
            text=True,
        )
        print(f"[SUCCESS] {description} completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {description} failed with exit code {e.returncode}")
        return False
    except Exception as e:
        print(f"[ERROR] {description} failed with error: {e}")
        return False


# Target allocation for biweekly portfolio; rebalance resets to this (P&L taken out)
BIWEEKLY_TARGET_NOTIONAL = 50_000.0


def main(
    force: bool = typer.Option(False, "--force", help="Run even if not Sunday"),
    notional: float = typer.Option(
        BIWEEKLY_TARGET_NOTIONAL,
        "--notional",
        help="Target notional in USD; portfolio %% and trades based on this (default 50,000)",
    ),
):
    """Run bi-weekly portfolio pipeline: build portfolio, write trades CSV, track biweekly performance only."""
    print("="*80)
    print("BIWEEKLY PORTFOLIO RUN")
    print("="*80)
    print(f"Date: {date.today()}")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print(f"Runs directory: {BIWEEKLY_RUNS_DIR.absolute()}")
    print()

    if not force and not is_biweekly_day():
        print("[WARN] Today is not the configured biweekly day (Monday). Use --force to run anyway.")
        return 1

    python_exe = sys.executable
    base_dir = Path(__file__).parent.parent

    # Step 1: Generate universe
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "universe", "generate"],
        "Generate Universe",
    ):
        return 1

    # Theme identification and merge (same as daily)
    if not run_command(
        [python_exe, str(base_dir / "main.py"), "themes", "identify"],
        "Identify Market Themes",
    ):
        print("[WARN] Theme identification failed, continuing without themes...")

    theme_candidates_file = base_dir / "data" / "theme_candidates.json"
    if (base_dir / "data" / "themes.json").exists():
        if not run_command(
            [python_exe, str(base_dir / "main.py"), "themes", "generate-candidates"],
            "Generate Theme-Based Candidates",
        ):
            print("[WARN] Theme candidate generation failed, continuing...")

    if theme_candidates_file.exists():
        if not run_command(
            [python_exe, str(base_dir / "main.py"), "universe", "merge"],
            "Merge Candidates",
        ):
            print("[WARN] Candidate merge failed, using regular candidates only...")
            merged_file = base_dir / "data" / "merged_candidates.json"
            regular_file = base_dir / "data" / "candidates.json"
            if regular_file.exists() and not merged_file.exists():
                shutil.copy2(regular_file, merged_file)
    else:
        merged_file = base_dir / "data" / "merged_candidates.json"
        regular_file = base_dir / "data" / "candidates.json"
        if regular_file.exists() and not merged_file.exists():
            shutil.copy2(regular_file, merged_file)

    merged_candidates_file = base_dir / "data" / "merged_candidates.json"
    if not run_command(
        [
            python_exe,
            str(base_dir / "main.py"),
            "data",
            "fetch",
            "--candidates-file",
            str(merged_candidates_file),
        ],
        "Fetch Stock Data",
    ):
        return 1

    if not run_command(
        [python_exe, str(base_dir / "main.py"), "score", "score"],
        "Score Candidates",
    ):
        return 1

    # Build portfolio into biweekly runs directory
    if not run_command(
        [
            python_exe,
            str(base_dir / "main.py"),
            "portfolio",
            "build",
            "--runs-base-dir",
            str(BIWEEKLY_RUNS_DIR),
        ],
        "Build Portfolio (biweekly)",
    ):
        return 1

    runs_dir = base_dir / BIWEEKLY_RUNS_DIR
    if not runs_dir.exists():
        print("[ERROR] No run folders found")
        return 1

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

    # Generate portfolio report (optional)
    report_file = latest_run / "portfolio_report.md"
    run_command(
        [
            python_exe,
            str(base_dir / "main.py"),
            "report",
            "generate",
            "--portfolio-file",
            str(portfolio_file),
            "--out",
            str(report_file),
        ],
        "Generate Portfolio Report",
    )

    # Write trades CSV: initial run = all Buy; subsequent = rebalance (Sells then Buys)
    print("\n" + "="*80)
    print("GENERATING TRADES CSV")
    print("="*80)
    csv_path = latest_run / "trades.csv"
    notional_val = notional
    notional_env = os.getenv("BIWEEKLY_NOTIONAL")
    if notional_env:
        try:
            notional_val = float(notional_env)
        except ValueError:
            pass
    csv_cmd = [
        python_exe,
        str(base_dir / "main.py"),
        "report",
        "trades-csv",
        "--portfolio-file",
        str(portfolio_file),
        "--out",
        str(csv_path),
        "--notional",
        str(notional_val),
    ]
    # If there's a previous biweekly run, pass it for rebalance (sell/buy deltas)
    if len(run_folders) > 1:
        previous_run_folder = run_folders[1]
        if (previous_run_folder / "portfolio.json").exists():
            csv_cmd += ["--previous-run", str(previous_run_folder)]
            print(f"  Rebalance mode: previous run {previous_run_folder.name}")
    if not run_command(csv_cmd, "Write Trades CSV"):
        print("[WARN] Trades CSV generation failed, continuing...")
    else:
        print(f"[OK] Trades CSV: {csv_path}")

    # Update P&L ledger from this run's period_pnl.json (rebalance runs only)
    pnl_ledger_path = runs_dir / "pnl_ledger.json"
    period_pnl_path = latest_run / "period_pnl.json"
    if period_pnl_path.exists():
        try:
            period_data = json.loads(period_pnl_path.read_text(encoding="utf-8"))
            ledger = {"entries": [], "cumulative_pnl": 0.0}
            if pnl_ledger_path.exists():
                ledger = json.loads(pnl_ledger_path.read_text(encoding="utf-8"))
            entry = {
                "run_folder": latest_run.name,
                "run_date": period_data.get("run_date", date.today().isoformat()),
                "period_pnl": period_data["period_pnl"],
                "portfolio_value_before_rebalance": period_data["portfolio_value_before_rebalance"],
                "target_notional": period_data["target_notional"],
            }
            ledger.setdefault("entries", []).append(entry)
            ledger["cumulative_pnl"] = sum(e["period_pnl"] for e in ledger["entries"])
            pnl_ledger_path.parent.mkdir(parents=True, exist_ok=True)
            pnl_ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
            print(f"[OK] P&L ledger updated: period ${entry['period_pnl']:+,.2f}, cumulative ${ledger['cumulative_pnl']:+,.2f}")
        except Exception as e:
            print(f"[WARN] Could not update P&L ledger: {e}")

    # Track performance of biweekly runs only (separate from daily)
    print("\n" + "="*80)
    print("TRACKING BIWEEKLY PORTFOLIO PERFORMANCE")
    print("="*80)
    if not run_command(
        [
            python_exe,
            str(base_dir / "main.py"),
            "performance",
            "track",
            "--runs-dir",
            str(runs_dir),
        ],
        "Track Biweekly Portfolio Performance",
    ):
        print("[WARN] Performance tracking failed, continuing...")

    # Optional: send biweekly report (trades CSV, P&L, beta/alpha, current portfolio) to BIWEEKLY_EMAIL_TO
    biweekly_to = os.getenv("BIWEEKLY_EMAIL_TO")
    if biweekly_to and biweekly_to.strip():
        print("\n" + "="*80)
        print("SENDING BIWEEKLY EMAIL REPORT")
        print("="*80)
        if not os.getenv("SMTP_PASSWORD"):
            print("[WARN] SMTP_PASSWORD not set, skipping biweekly email")
        else:
            email_cmd = [
                python_exe,
                str(base_dir / "main.py"),
                "email",
                "send-biweekly",
                "--portfolio-file",
                str(portfolio_file),
                "--email-to",
                biweekly_to.strip(),
            ]
            if not run_command(email_cmd, "Send Biweekly Email Report"):
                print("[WARN] Biweekly email failed, continuing...")
    else:
        print("\n[INFO] BIWEEKLY_EMAIL_TO not set; skipping email (use BIWEEKLY_EMAIL_TO to send to a distinct list).")

    print("\n[INFO] Biweekly run complete. No submission to MAYS (by design).")
    return 0


if __name__ == "__main__":
    typer.run(main)
