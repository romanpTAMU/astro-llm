# News Fetching Analysis & Improvements

## Current Implementation

### News Sources (Tiered Approach)
1. **Finnhub API** (Tier 1)
   - Last 30 days
   - Returns: Up to `max_items` (default: 10)
   - API Limit: 60 calls/minute (free tier)
   - Quality: High, ticker-specific

2. **FMP General News API** (Tier 2)
   - Last 30 days
   - Fetches: Up to `max_items * 2` (max 250) general news
   - Filters: By ticker mention in title/text
   - Returns: Up to `max_items`
   - API Limit: Varies by plan
   - Quality: Medium (general news filtered by ticker)

3. **Alpha Vantage** (Tier 3)
   - Recent news
   - Returns: Up to `max_items` (API limit: 50)
   - Includes: Built-in sentiment analysis
   - API Limit: 5 calls/minute, 500/day (free tier)
   - Quality: High, includes sentiment

4. **LLM Web Search** (Tier 4)
   - Last 30 days
   - Returns: Remaining slots up to `max_items`
   - Uses: OpenAI with web search enabled
   - Quality: Variable, can find recent news

### Current Limits
- **Default**: `max_items = 10` per ticker
- **Date Range**: Last 30 days (hardcoded)
- **Total Possible**: Up to 10 items per ticker (could be more with better aggregation)

## Issues & Improvements

### 1. Low Default Limit
**Problem**: Only 10 news items per ticker may miss important information.

**Solution**: Increase default to 15-20 items:
```python
max_items: int = 20  # Increased from 10
```

### 2. No Deduplication
**Problem**: Same news article from different sources counted multiple times.

**Solution**: Add deduplication by headline similarity:
```python
def deduplicate_news(news_items: list[NewsItem]) -> list[NewsItem]:
    """Remove duplicate news items based on headline similarity."""
    seen = set()
    unique = []
    for item in news_items:
        # Normalize headline for comparison
        normalized = item.headline.lower().strip()
        # Simple deduplication (could use fuzzy matching for better results)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(item)
    return unique
```

### 3. Inefficient FMP Filtering
**Problem**: FMP fetches general news (up to 250 items) then filters by ticker mention, which is wasteful.

**Solution**: 
- Option A: Use FMP's ticker-specific endpoint if available
- Option B: Increase the limit we request to get more ticker-specific results
- Option C: Keep current approach but optimize filtering

### 4. Hardcoded 30-Day Window
**Problem**: 30 days may be too long (stale news) or too short (miss important context).

**Solution**: Make it configurable:
```python
news_lookback_days: int = Field(14, alias="NEWS_LOOKBACK_DAYS")  # Default 14 days
```

### 5. Missing News Aggregation
**Problem**: We stop at first API that returns enough items, missing potentially better sources.

**Solution**: Fetch from all available sources, then aggregate and deduplicate:
```python
# Fetch from all sources in parallel
all_news = []
if finnhub_key:
    all_news.extend(fetch_news_finnhub(...))
if fmp_key:
    all_news.extend(fetch_news_fmp(...))
if alpha_vantage_key:
    all_news.extend(fetch_news_alpha_vantage(...))

# Deduplicate and sort
all_news = deduplicate_news(all_news)
all_news = sorted(all_news, key=lambda x: x.published_at or datetime.min, reverse=True)

# Return top N
return all_news[:max_items]
```

### 6. No Quality Scoring
**Problem**: All news items treated equally, regardless of source quality or relevance.

**Solution**: Add quality scoring:
```python
def score_news_quality(item: NewsItem) -> float:
    """Score news item quality (0-1)."""
    score = 0.5  # Base score
    
    # Source quality
    high_quality_sources = ['Reuters', 'Bloomberg', 'WSJ', 'Financial Times']
    if any(source in item.source for source in high_quality_sources):
        score += 0.2
    
    # Recency (more recent = higher score)
    if item.published_at:
        days_old = (datetime.now() - item.published_at).days
        recency_score = max(0, 1 - (days_old / 30))
        score += recency_score * 0.2
    
    # Has sentiment (from Alpha Vantage)
    if item.sentiment:
        score += 0.1
    
    return min(1.0, score)
```

### 7. Rate Limiting
**Problem**: No explicit rate limiting between API calls.

**Solution**: Already has `time.sleep(delay)` but could be smarter:
- Respect API rate limits
- Parallel fetching where possible
- Exponential backoff on errors

## Recommended Improvements

### Priority 1: Increase Default & Add Deduplication
```python
# In data_fetcher.py
max_items: int = 20  # Increased from 10

def fetch_news_tiered(...):
    # ... existing code ...
    
    # Deduplicate before returning
    news_items = deduplicate_news(news_items)
    return news_items[:max_items]
```

### Priority 2: Make Date Range Configurable
```python
# In config.py
news_lookback_days: int = Field(14, alias="NEWS_LOOKBACK_DAYS")

# In data_apis.py
def fetch_news_finnhub(..., lookback_days: int = 14):
    from_date = (effective_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
```

### Priority 3: Aggregate All Sources
Instead of stopping at first source with enough items, fetch from all and aggregate.

### Priority 4: Add Quality Scoring
Rank news by quality/relevance before returning.

## API Limits Summary

| Source | Free Tier Limit | Paid Tier |
|--------|----------------|-----------|
| Finnhub | 60 calls/min | Higher limits |
| FMP | Varies | Higher limits |
| Alpha Vantage | 5 calls/min, 500/day | Higher limits |
| OpenAI | Rate limits apply | Higher limits |

## Current Capacity

For 60 candidates:
- **Current**: 10 items × 60 tickers = 600 news items total
- **With 20 items**: 20 × 60 = 1,200 news items total
- **Time**: ~60-120 seconds (with delays)

## Testing Recommendations

1. Test with `max_items=20` to see if quality improves
2. Add deduplication to avoid duplicate news
3. Monitor API rate limits
4. Consider caching news for same-day runs

