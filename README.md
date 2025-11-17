# astro-llm

LLM-powered investing agent. Phase 1 builds a US equities candidate universe under given constraints.

## Setup

1. Python 3.11+
2. Create venv and install deps:
   - PowerShell:
     - `python -m venv .venv`
     - `./.venv/Scripts/Activate.ps1`
     - `pip install -r requirements.txt`
3. Configure environment:
   - Copy `env.example` → `.env` (or set env vars directly)
   - Set `OPENAI_API_KEY`, optionally `OPENAI_MODEL`

## Phase 1: Universe & constraints

### Regular Universe Generation

Windows PowerShell (from repo root):
```powershell
python main.py universe generate --out data/candidates.json
```

Options:
- `--count 60` override candidate count
- `--model gpt-4o-mini` override model

Outputs:
- `data/candidates.json` with JSON schema:
  ```json
  {
    "candidates": [
      {"ticker": "AAPL", "sector": "Information Technology", "rationale": "..."}
    ]
  }
  ```

### Theme-Based Candidate Generation

1. **Identify market themes:**
   ```powershell
   python main.py themes identify --out data/themes.json
   ```
   Outputs `data/themes.json` with 5-8 major market themes.

2. **Generate theme-based candidates:**
   ```powershell
   python main.py themes generate-candidates --themes-file data/themes.json --out data/theme_candidates.json
   ```
   Outputs `data/theme_candidates.json` with 3-5 candidates per theme (15-40 total).

3. **Merge regular + theme candidates:**
   ```powershell
   python main.py universe merge --regular data/candidates.json --themes data/theme_candidates.json --out data/merged_candidates.json
   ```
   Combines both sets, with theme candidates taking precedence on duplicates.

### Full Workflow Example

```powershell
# Generate regular candidates
python main.py universe generate --out data/candidates.json

# Identify themes
python main.py themes identify --out data/themes.json

# Generate theme candidates
python main.py themes generate-candidates --themes-file data/themes.json --out data/theme_candidates.json

# Merge everything
python main.py universe merge --out data/final_candidates.json
```

Notes:
- Theme candidates include a `theme` field linking them to the identified market themes.
- The model proposes candidates; later phases will validate tickers, prices, and apply hard screens.

## Phase 2: Data Acquisition

Fetch price, fundamentals, analyst recommendations, and news for all candidates.

### Fetch Stock Data

```powershell
python main.py data fetch --candidates-file data/candidates.json --out data/stock_data.json
```

Options:
- `--skip-news` - Skip news fetching (faster, reduces API calls)
- `--skip-analyst` - Skip analyst recommendations
- `--delay 0.5` - Delay between API calls (seconds, default 0.5)
- `--model gpt-4o-mini` - Override model for analyst/news extraction

### What Gets Fetched

1. **Price Data** (Tiered approach):
   - **Tier 1:** Finnhub API (free, real-time) - if `FINNHUB_API_KEY` is set
   - **Tier 2:** yfinance (may be blocked by Yahoo Finance)
   - Provides: Current price, volume, market cap, price change %
   - Note: Yahoo Finance has restricted access as of 2025, so Finnhub is recommended

2. **Fundamentals** (via FMP API):
   - Revenue (TTM)
   - Revenue YoY growth
   - Operating margin
   - FCF margin
   - ROIC
   - P/E ratio
   - EV/EBITDA
   - Net debt to EBITDA
   - Requires `FMP_API_KEY` in `.env`

3. **Analyst Recommendations** (Tiered approach):
   - **Tier 1:** Finnhub API (free, real-time) - if `FINNHUB_API_KEY` is set
   - **Tier 2:** LLM with web search (GPT-5 web_search_options) - fallback
   - Provides: Consensus rating, price targets, analyst count, recent changes

4. **News** (Tiered approach):
   - **Tier 1:** Finnhub API (free, real-time) - if `FINNHUB_API_KEY` is set
   - **Tier 2:** Alpha Vantage API (free, includes sentiment) - if `ALPHA_VANTAGE_API_KEY` is set
   - **Tier 3:** LLM with web search (GPT-5 web_search_options) - fallback
   - Provides: Headlines, summaries, sources, URLs, sentiment classification

### Output Format

```json
{
  "data": [
    {
      "ticker": "AAPL",
      "price_data": {
        "ticker": "AAPL",
        "price": 175.50,
        "volume": 50000000,
        "avg_volume_30d": 45000000,
        "market_cap": 2800000000000,
        "price_change_pct": 1.2,
        "as_of": "2025-01-XX..."
      },
      "fundamentals": { ... },
      "analyst_recommendations": { ... },
      "news": [ ... ]
    }
  ],
  "fetched_at": "2025-01-XX..."
}
```

### API Keys Setup

1. **Finnhub** (recommended): Get free API key at https://finnhub.io
   - Free tier: 60 calls/minute
   - Provides analyst recommendations and news

2. **Alpha Vantage** (optional): Get free API key at https://www.alphavantage.co
   - Free tier: 5 calls/minute, 500 calls/day
   - Provides news with built-in sentiment analysis

3. **Financial Modeling Prep** (recommended for fundamentals): Get API key at https://financialmodelingprep.com
   - Free tier: 250 calls/day
   - Paid plan: 300 calls/minute (rate limiting enforced automatically)
   - Provides income statements, balance sheets, cash flow, and financial ratios

4. Add to `.env` file:
   ```
   FINNHUB_API_KEY=your_key_here
   ALPHA_VANTAGE_API_KEY=your_key_here
   FMP_API_KEY=your_key_here
   ```

### Performance Notes

- **Price Data:**
  - With Finnhub: 60 API calls (free, fast, reliable)
  - Without Finnhub: yfinance fallback (may be blocked by Yahoo Finance)
- **Fundamentals:**
  - With FMP: 180 API calls total for 60 tickers (3 calls per ticker: income statement + ratios + cash flow)
  - Free tier: 250 calls/day (fits within free tier with buffer)
  - Paid plan: Higher limits available if needed
  - Without FMP: Skipped
- **Analyst Recs:** 
  - With Finnhub: 60 API calls (free, fast)
  - Without Finnhub: 60 LLM calls with web search (costs tokens, slower)
- **News:**
  - With Finnhub: 60 API calls (free, fast)
  - With Alpha Vantage only: 60 API calls (free, slower due to rate limits)
  - Without APIs: 60 LLM calls with web search (costs tokens, slower)
- Use `--skip-news` or `--skip-analyst` to reduce calls

## Phase 3: Analysis & Scoring

Score candidates using factor analysis, sentiment synthesis, and risk screens.

### Score Candidates

```powershell
python main.py score score --stock-data-file data/stock_data.json --candidates-file data/candidates.json --out data/scored_candidates.json
```

Or shorter:
```powershell
python main.py score score --out data/scored_candidates.json
```

Options:
- `--stock-data-file` - Input stock data JSON (from Phase 2)
- `--candidates-file` - Original candidates file (for sector/theme info)
- `--out` - Output JSON path
- `--model gpt-4o-mini` - Override model for sentiment synthesis

### What Gets Calculated

1. **Factor Scores** (normalized 0-1 scale):
   - **Value**: EV/EBITDA, P/E ratio, FCF yield
   - **Quality**: ROIC, operating margins
   - **Growth**: Revenue YoY growth
   - **Stability**: Price volatility (simplified)
   - **Revisions**: Analyst consensus and recent changes

2. **Sentiment Synthesis** (via LLM):
   - Combines analyst recommendations and news sentiment
   - Provides overall sentiment (bullish/neutral/bearish)
   - Identifies key drivers and risks
   - Calculates price target upside

3. **Risk Screens** (hard filters):
   - Liquidity check (minimum dollar volume)
   - Price check (minimum $3)
   - Trading halts (placeholder)
   - Pending M&A (placeholder)
   - Earnings calendar (placeholder)

4. **Composite Score**:
   - Weighted combination of all factors
   - Stocks that fail risk screens get score of -10
   - Scores sorted descending for ranking

### Output Format

```json
{
  "candidates": [
    {
      "ticker": "AAPL",
      "sector": "Information Technology",
      "theme": "AI Infrastructure Buildout",
      "factor_scores": {
        "value": 0.5,
        "quality": 0.8,
        "growth": 0.3,
        "stability": 0.5,
        "revisions": 0.6
      },
      "sentiment": {
        "overall_sentiment": "bullish",
        "sentiment_score": 0.7,
        "key_drivers": ["Strong iPhone cycle", "Services growth"],
        "key_risks": ["China exposure", "Regulatory concerns"]
      },
      "risk_flags": {
        "passed_all_checks": true,
        "failed_checks": []
      },
      "composite_score": 0.65
    }
  ],
  "stats": {
    "total_scored": 60,
    "passed_risk_screens": 55,
    "avg_composite_score": 0.42
  }
}
```

### Scoring Weights

Composite score uses weighted factors:
- Value: 20%
- Quality: 25%
- Growth: 20%
- Stability: 10%
- Revisions: 10%
- Sentiment: 15%

