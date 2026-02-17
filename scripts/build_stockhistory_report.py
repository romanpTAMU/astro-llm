#!/usr/bin/env python3
"""
Build an Excel workbook that uses STOCKHISTORY() formulas (Microsoft 365) to pull
daily performance by period. Formulas only—no precomputed values; Excel evaluates
when the file is opened. Uses xlsxwriter with write_dynamic_array_formula() so
Excel does not add @ and show #NAME?. Optionally computes Beta per period via FMP
and writes it into the Beta row.

Usage: python scripts/build_stockhistory_report.py [--from-month YYYY-MM] [--to-month YYYY-MM] [--out path]
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Optional

import typer
import xlsxwriter
from xlsxwriter.utility import xl_col_to_name, xl_rowcol_to_cell

import sys
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Reuse period/portfolio logic (minimal copy to avoid API dependency)
def parse_run_date(folder_name: str) -> Optional[date]:
    m = re.match(r"(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}-\d{2}", folder_name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def find_run_closest_to_day(runs_with_dates: list, target_day: int, month: date) -> Optional[tuple]:
    candidates = [(p, d) for p, d in runs_with_dates if d.year == month.year and d.month == month.month]
    if not candidates:
        return None
    return min(candidates, key=lambda x: abs(x[1].day - target_day))


def get_rebalance_runs(runs_dir: Path) -> list:
    if not runs_dir.exists():
        return []
    out = []
    for folder in runs_dir.iterdir():
        if not folder.is_dir() or not (folder / "portfolio.json").exists():
            continue
        d = parse_run_date(folder.name)
        if d is not None:
            out.append((folder, d))
    out.sort(key=lambda x: x[1])
    return out


def load_portfolio(run_folder: Path) -> list:
    p = run_folder / "portfolio.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("holdings", [])
    except Exception:
        return []


def build_monthly_periods(runs_with_dates: list) -> list:
    if len(runs_with_dates) < 2:
        return []
    periods = []
    month_keys = sorted(set((d.year, d.month) for _, d in runs_with_dates))
    for year, month in month_keys:
        month_date = date(year, month, 1)
        month_label = month_date.strftime("%B %Y")
        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1
        next_month_date = date(next_year, next_month, 1)
        run_2nd = find_run_closest_to_day(runs_with_dates, 2, month_date)
        run_16th = find_run_closest_to_day(runs_with_dates, 16, month_date)
        run_next_2nd = find_run_closest_to_day(runs_with_dates, 2, next_month_date)
        if run_2nd and run_16th:
            (sf, sd), (ef, ed) = run_2nd, run_16th
            if sd < ed:
                periods.append((f"{month_label} (2nd-16th)", sf, ef, sd, ed))
        if run_16th and run_next_2nd:
            (sf, sd), (ef, ed) = run_16th, run_next_2nd
            if sd < ed:
                periods.append((f"{month_label} (16th-2nd)", sf, ef, sd, ed))
    return periods


def _aligned_daily_returns(
    port_cum: list[tuple[date, float]],
    market_cum: list[tuple[date, float]],
) -> tuple[list[float], list[float]]:
    """Return (port_daily_returns, market_daily_returns) for dates in both series."""
    port_by_date = dict(port_cum)
    market_by_date = dict(market_cum)
    common_dates = sorted(set(port_by_date) & set(market_by_date))
    if len(common_dates) < 2:
        return [], []
    port_rets, market_rets = [], []
    for i in range(1, len(common_dates)):
        d_prev, d_curr = common_dates[i - 1], common_dates[i]
        p_prev, p_curr = port_by_date[d_prev], port_by_date[d_curr]
        m_prev, m_curr = market_by_date[d_prev], market_by_date[d_curr]
        port_rets.append((p_curr / p_prev) - 1.0 if p_prev else 0.0)
        market_rets.append((m_curr / m_prev) - 1.0 if m_prev else 0.0)
    return port_rets, market_rets


def compute_beta(
    port_cum: list[tuple[date, float]],
    market_cum: list[tuple[date, float]],
) -> Optional[float]:
    """Beta = Cov(port daily ret, market daily ret) / Var(market daily ret)."""
    port_rets, market_rets = _aligned_daily_returns(port_cum, market_cum)
    if len(port_rets) < 2:
        return None
    n = len(port_rets)
    mean_p = sum(port_rets) / n
    mean_m = sum(market_rets) / n
    cov = sum((p - mean_p) * (m - mean_m) for p, m in zip(port_rets, market_rets)) / (n - 1)
    var_m = sum((m - mean_m) ** 2 for m in market_rets) / (n - 1)
    if var_m == 0:
        return None
    return cov / var_m


def _portfolio_cumulative(
    holdings: list,
    daily_series: dict[str, list[tuple[date, float]]],
) -> list[tuple[date, float]]:
    """Weighted portfolio cumulative return series (same logic as daily_performance_report)."""
    if not holdings or not daily_series:
        return []
    all_dates = set()
    for series in daily_series.values():
        for d, _ in series:
            all_dates.add(d)
    sorted_dates = sorted(all_dates)
    if not sorted_dates:
        return []
    weight_by_ticker = {h["ticker"]: h.get("weight", 0) for h in holdings}
    total_weight = sum(weight_by_ticker.values()) or 1
    result = []
    for d in sorted_dates:
        cum = 0.0
        for ticker, series in daily_series.items():
            date_to_cum = dict(series)
            if d in date_to_cum:
                w = weight_by_ticker.get(ticker, 0) / total_weight
                cum += w * date_to_cum[d]
        result.append((d, cum))
    return result


def write_period_sheet(
    ws: "xlsxwriter.Worksheet",
    label: str,
    holdings: list,
    period_start: date,
    period_end: date,
    beta: Optional[float] = None,
    date_fmt: Optional[xlsxwriter.format.Format] = None,
) -> None:
    """One sheet per period: G1=start, H1=end; A=ticker, B=weight, C=return formula, D+=spill. All formulas, no values."""
    # Row/col 0-based. G1=(0,6), H1=(0,7)
    ws.write_datetime(0, 6, datetime.combine(period_start, dt_time.min), date_fmt)  # G1
    ws.write_datetime(0, 7, datetime.combine(period_end, dt_time.min), date_fmt)   # H1
    ws.write(0, 0, "Ticker")
    ws.write(0, 1, "Weight")
    ws.write(0, 2, "Return")
    n = len(holdings)
    # Holdings rows 1..n (0-based: 1 to n)
    for i in range(n):
        row = 1 + i
        h = holdings[i]
        ticker = h.get("ticker", "")
        weight = h.get("weight", 0)
        ticker_cell = xl_rowcol_to_cell(row, 0)
        ws.write(row, 0, ticker)
        ws.write_number(row, 1, weight)
        # Period return: INDEX(TRANSPOSE(STOCKHISTORY(...)),1,COLUMNS(...)) — scalar formula
        base = f"TRANSPOSE(STOCKHISTORY({ticker_cell},$G$1,$H$1,0,0,1)/STOCKHISTORY({ticker_cell},$G$1,$G$1,0,0,1))"
        ws.write_formula(row, 2, f"=INDEX({base},1,COLUMNS({base}))")
        # Cumulative row: spills — use dynamic array formula so Excel does not add @
        cum_formula = f"=TRANSPOSE(STOCKHISTORY({ticker_cell},$G$1,$H$1,0,0,1)/STOCKHISTORY({ticker_cell},$G$1,$G$1,0,0,1))"
        ws.write_dynamic_array_formula(xl_rowcol_to_cell(row, 3) + ":" + xl_rowcol_to_cell(row, 3), cum_formula)
    # Total Return row
    total_row = n + 1
    ws.write(total_row, 0, "Total Return")
    ws.write(total_row, 1, "")
    ws.write_formula(total_row, 2, f"=SUMPRODUCT(B2:B{n+1},C2:C{n+1})/SUM(B2:B{n+1})")
    for col in range(3, 14):  # D through N (11 columns)
        col_letter = xl_col_to_name(col)
        ws.write_formula(total_row, col, f"=SUMPRODUCT($B$2:$B${n+1},{col_letter}2:{col_letter}{n+1})/SUM($B$2:$B${n+1})")
    # SPY row
    sp_row = total_row + 1
    sp_ticker_cell = xl_rowcol_to_cell(sp_row, 0)
    ws.write(sp_row, 0, "SPY")
    ws.write(sp_row, 1, "")
    base_sp = f"TRANSPOSE(STOCKHISTORY({sp_ticker_cell},$G$1,$H$1,0,0,1)/STOCKHISTORY({sp_ticker_cell},$G$1,$G$1,0,0,1))"
    ws.write_formula(sp_row, 2, f"=INDEX({base_sp},1,COLUMNS({base_sp}))")
    ws.write_dynamic_array_formula(xl_rowcol_to_cell(sp_row, 3) + ":" + xl_rowcol_to_cell(sp_row, 3),
        f"=TRANSPOSE(STOCKHISTORY({sp_ticker_cell},$G$1,$H$1,0,0,1)/STOCKHISTORY({sp_ticker_cell},$G$1,$G$1,0,0,1))")
    # Excess, Beta, Alpha
    excess_row = sp_row + 2
    ws.write(excess_row, 0, "Excess Return")
    # Excel row numbers are 1-based: total_row/sp_row are 0-based
    excel_total = total_row + 1
    excel_spy = sp_row + 1
    ws.write_formula(excess_row, 1, f"=C{excel_total}-C{excel_spy}")
    beta_row = sp_row + 3
    ws.write(beta_row, 0, "Beta")
    if beta is not None:
        ws.write_number(beta_row, 1, round(beta, 10))
    alpha_row = sp_row + 4
    excel_beta = beta_row + 1
    ws.write_formula(alpha_row, 1, f"=C{excel_total}-C{excel_spy}*B{excel_beta}")


def main(
    runs_dir: Path = typer.Option(Path("data/runs"), "--runs-dir"),
    out: Path = typer.Option(Path("data/daily_performance_stockhistory.xlsx"), "--out"),
    from_month: Optional[str] = typer.Option(None, "--from-month"),
    to_month: Optional[str] = typer.Option(None, "--to-month"),
    compute_beta_api: bool = typer.Option(True, "--compute-beta/--no-compute-beta", help="Compute Beta via FMP (requires FMP_API_KEY)"),
) -> None:
    runs_dir = Path(runs_dir)
    runs_base = runs_dir if runs_dir.is_absolute() else PROJECT_ROOT / runs_dir
    runs_with_dates = get_rebalance_runs(runs_base)
    if not runs_with_dates:
        typer.echo(f"[ERROR] No runs with portfolio.json in {runs_base}")
        raise typer.Exit(1)
    periods = build_monthly_periods(runs_with_dates)
    if from_month:
        try:
            y, m = map(int, from_month.split("-"))
            periods = [p for p in periods if p[3] >= date(y, m, 1)]
        except ValueError:
            pass
    if to_month:
        try:
            y, m = map(int, to_month.split("-"))
            periods = [p for p in periods if p[3] <= date(y, m, 28)]
        except ValueError:
            pass
    if not periods:
        typer.echo("[ERROR] No periods found")
        raise typer.Exit(1)
    fmp_key = None
    if compute_beta_api:
        try:
            from agent.config import load_config
            cfg = load_config()
            fmp_key = cfg.fmp_api_key
        except Exception:
            pass
        if not fmp_key:
            typer.echo("[INFO] FMP_API_KEY not set; Beta row left blank (use --no-compute-beta to skip)")
    out_path = PROJECT_ROOT / out if not out.is_absolute() else Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # xlsxwriter: use_future_functions so newer functions (e.g. STOCKHISTORY) are stored correctly; dynamic array formulas avoid @
    wb = xlsxwriter.Workbook(str(out_path), {"use_future_functions": True})
    date_fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    for label, start_folder, _ef, period_start, period_end in periods:
        holdings = load_portfolio(start_folder)
        if not holdings:
            continue
        beta_val = None
        if fmp_key:
            try:
                from agent.data_apis import fetch_historical_daily_series
                daily_series = {}
                tickers = [h["ticker"] for h in holdings if h.get("ticker")]
                for ticker in tickers:
                    series = fetch_historical_daily_series(ticker, period_start, period_end, fmp_key)
                    if not series:
                        daily_series[ticker] = []
                        continue
                    first = series[0][1]
                    if first == 0:
                        daily_series[ticker] = []
                        continue
                    daily_series[ticker] = [(d, c / first) for d, c in series]
                    time.sleep(0.25)
                port_cum = _portfolio_cumulative(holdings, daily_series)
                spy_series = fetch_historical_daily_series("SPY", period_start, period_end, fmp_key)
                time.sleep(0.25)
                if spy_series:
                    first_spy = spy_series[0][1]
                    if first_spy:
                        sp500_cum = [(d, c / first_spy) for d, c in spy_series]
                        beta_val = compute_beta(port_cum, sp500_cum)
            except Exception as e:
                typer.echo(f"  [WARN] Beta for {label}: {e}")
        ws = wb.add_worksheet(name=label[:31])
        write_period_sheet(ws, label, holdings, period_start, period_end, beta=beta_val, date_fmt=date_fmt)
        typer.echo(f"Sheet: {label} ({period_start} to {period_end})" + (f" Beta={beta_val:.4f}" if beta_val is not None else ""))
    wb.close()
    typer.echo(f"[SUCCESS] Wrote {out_path} (open in Microsoft 365 Excel; formulas only, use STOCKHISTORY)")


if __name__ == "__main__":
    typer.run(main)
