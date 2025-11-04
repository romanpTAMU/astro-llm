"""Diagnostic script to analyze analyst recommendation distribution."""
from pathlib import Path
import json
import typer
from collections import Counter
from typing import Dict, List

app = typer.Typer()


@app.command()
def analyze(
    stock_data_file: Path = typer.Option(
        Path("data/stock_data.json"), help="Input stock data JSON path"
    ),
):
    """Analyze analyst recommendation distribution."""
    if not stock_data_file.exists():
        typer.echo(f"Stock data file not found: {stock_data_file}")
        raise typer.Exit(code=1)
    
    stock_data_text = stock_data_file.read_text(encoding='utf-8')
    if not stock_data_text or not stock_data_text.strip():
        typer.echo(f"Stock data file is empty: {stock_data_file}")
        raise typer.Exit(code=1)
    
    try:
        stock_data_json = json.loads(stock_data_text)
    except json.JSONDecodeError as e:
        typer.echo(f"Invalid JSON: {e}")
        raise typer.Exit(code=1)
    
    from .models import StockDataResponse
    stock_data_resp = StockDataResponse.model_validate(stock_data_json)
    
    # Collect statistics
    consensus_counts = Counter()
    buy_counts = []
    hold_counts = []
    sell_counts = []
    total_counts = []
    buy_pcts = []
    hold_pcts = []
    sell_pcts = []
    tickers_with_counts = []  # Track tickers that have counts
    
    tickers_with_data = 0
    tickers_without_data = 0
    
    for stock_data in stock_data_resp.data:
        if stock_data.analyst_recommendations:
            tickers_with_data += 1
            rec = stock_data.analyst_recommendations
            consensus_counts[rec.consensus or "Unknown"] += 1
            
            if rec.buy_count is not None and rec.hold_count is not None and rec.sell_count is not None:
                tickers_with_counts.append(stock_data.ticker)
                buy_counts.append(rec.buy_count)
                hold_counts.append(rec.hold_count)
                sell_counts.append(rec.sell_count)
                total = rec.buy_count + rec.hold_count + rec.sell_count
                total_counts.append(total)
                
                if total > 0:
                    buy_pcts.append((rec.buy_count / total) * 100)
                    hold_pcts.append((rec.hold_count / total) * 100)
                    sell_pcts.append((rec.sell_count / total) * 100)
        else:
            tickers_without_data += 1
    
    # Print statistics
    typer.echo("\n" + "=" * 60)
    typer.echo("ANALYST RECOMMENDATION DISTRIBUTION ANALYSIS")
    typer.echo("=" * 60)
    
    typer.echo(f"\n[COVERAGE]")
    typer.echo(f"  Tickers with analyst data: {tickers_with_data}")
    typer.echo(f"  Tickers without analyst data: {tickers_without_data}")
    typer.echo(f"  Total tickers: {len(stock_data_resp.data)}")
    
    typer.echo(f"\n[CONSENSUS DISTRIBUTION]")
    total_with_consensus = sum(consensus_counts.values())
    for consensus, count in consensus_counts.most_common():
        pct = (count / total_with_consensus * 100) if total_with_consensus > 0 else 0
        typer.echo(f"  {consensus}: {count} ({pct:.1f}%)")
    
    if buy_counts:
        typer.echo(f"\n[RECOMMENDATION COUNTS] (averages):")
        avg_buy = sum(buy_counts) / len(buy_counts)
        avg_hold = sum(hold_counts) / len(hold_counts)
        avg_sell = sum(sell_counts) / len(sell_counts)
        avg_total = sum(total_counts) / len(total_counts)
        
        typer.echo(f"  Average Buy: {avg_buy:.1f}")
        typer.echo(f"  Average Hold: {avg_hold:.1f}")
        typer.echo(f"  Average Sell: {avg_sell:.1f}")
        typer.echo(f"  Average Total Analysts: {avg_total:.1f}")
        
        typer.echo(f"\n[RECOMMENDATION PERCENTAGES] (averages):")
        avg_buy_pct = sum(buy_pcts) / len(buy_pcts)
        avg_hold_pct = sum(hold_pcts) / len(hold_pcts)
        avg_sell_pct = sum(sell_pcts) / len(sell_pcts)
        
        typer.echo(f"  Average Buy %: {avg_buy_pct:.1f}%")
        typer.echo(f"  Average Hold %: {avg_hold_pct:.1f}%")
        typer.echo(f"  Average Sell %: {avg_sell_pct:.1f}%")
        
        # Calculate Buy/Sell ratio
        if avg_sell_pct > 0:
            buy_sell_ratio = avg_buy_pct / avg_sell_pct
            typer.echo(f"\n[WARN] Buy/Sell Ratio: {buy_sell_ratio:.1f}x")
            typer.echo(f"   (Industry average is typically ~5-10x due to analyst bias)")
        
        # Identify most optimistic and pessimistic
        if buy_pcts and tickers_with_counts:
            max_buy_idx = buy_pcts.index(max(buy_pcts))
            min_buy_idx = buy_pcts.index(min(buy_pcts))
            
            max_buy_ticker = tickers_with_counts[max_buy_idx]
            min_buy_ticker = tickers_with_counts[min_buy_idx]
            
            typer.echo(f"\n[MOST OPTIMISTIC] {max_buy_ticker} ({max(buy_pcts):.1f}% Buy)")
            typer.echo(f"[MOST PESSIMISTIC] {min_buy_ticker} ({min(buy_pcts):.1f}% Buy)")
    
    typer.echo("\n" + "=" * 60)
    typer.echo("NOTE: Wall Street analysts are known to issue more Buy ratings")
    typer.echo("than Sell ratings due to industry bias (relationship management,")
    typer.echo("investment banking conflicts, etc.). This is a real phenomenon,")
    typer.echo("not necessarily a model issue.")
    typer.echo("=" * 60 + "\n")


if __name__ == "__main__":
    app()

