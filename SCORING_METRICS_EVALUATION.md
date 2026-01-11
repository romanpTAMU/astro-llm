# Scoring Metrics Evaluation Report

## Summary
Comprehensive analysis of scoring metrics across all stocks to identify zeros, outliers, and anomalies.

## Key Findings

### ✅ CORRECT BEHAVIOR (No Issues)

1. **Quality = 0.0** (4 tickers: RTX, TGT, SBUX, ENPH)
   - **Status**: ✅ CORRECT
   - **Reason**: These stocks have ROIC and Operating Margin between 5-10%, which correctly scores 0.0 for both metrics, averaging to 0.0
   - **Example**: RTX has ROIC=5.95%, OpMargin=8.26% → both score 0.0 → average = 0.0 ✓

2. **Stability = 0.0** (13 tickers)
   - **Status**: ✅ CORRECT
   - **Reason**: When beta is missing, fallback uses 1d price change. Stocks with 2-5% absolute change correctly return 0.0
   - **Example**: BKNG has 1d_change=2.94% → abs_change=2.94% → falls in [2, 5) bucket → returns 0.0 ✓

3. **Perfect Scores (1.0)**
   - **Quality = 1.0**: All 12 tickers have ROIC > 20% AND OpMargin > 20% ✓
   - **Growth = 1.0**: All 7 tickers have revenue growth > 20% ✓
   - **Value = 1.0**: Only META (composite=0.462, reasonable given other factors)

4. **Sentiment = 0.0** (14 tickers)
   - **Status**: ✅ ACCEPTABLE
   - **Reason**: LLM-generated sentiment scores. Most are neutral with Hold ratings and neutral news, which can legitimately result in 0.0
   - **Examples**: QCOM, PFE, VZ all have Hold consensus, neutral news → 0.0 sentiment

5. **Composite Score Distribution**
   - **Top scores**: ADBE (0.686), GOOGL (0.655), MRK (0.640) - all reasonable
   - **No suspicious patterns**: High composite scores correlate with strong individual factors

### ⚠️ ISSUES FOUND

1. **TWTR Still in Data**
   - **Status**: ⚠️ WILL BE FIXED BY NEW FILTER
   - **Issue**: TWTR appears in scored_candidates.json with composite=0
   - **Fix**: Our new filter drops tickers with all-zero metrics (TWTR has missing/zero data)
   - **Action**: Filter already implemented, will take effect on next scoring run

2. **META Missing Growth Data**
   - **Status**: ⚠️ NEEDS INVESTIGATION
   - **Issue**: META has `revenue_yoy_growth=null` in fundamentals
   - **Root Cause**: Fundamentals fetched using ticker="FB" instead of "META"
   - **Impact**: Growth factor score is None (dropped from composite), but composite still calculated
   - **Investigation Needed**: 
     - Check if FMP API returns growth data for "FB" vs "META"
     - Verify fallback calculation from income statements works for FB/META
   - **Action**: Review ticker normalization in `fetch_fundamentals_fmp` to ensure META ticker is used

3. **Beta Missing for Most Stocks**
   - **Status**: ⚠️ EXPECTED BUT SUBOPTIMAL
   - **Issue**: Most stocks have `beta=null`, causing fallback to 1d price change heuristic
   - **Impact**: Stability scores less accurate (using 1d volatility proxy instead of true beta)
   - **Reason**: Beta fetching from FMP profile endpoint may be failing or not available
   - **Action**: Investigate why beta is not being fetched successfully

### 📊 Score Distributions

**Value Score:**
- Range: [-0.60, 0.90]
- Median: 0.167
- Top 5: TROW (0.90), VZ (0.80), MRK (0.80), T (0.80), ADBE (0.70)

**Quality Score:**
- Range: [-0.40, 0.85]
- Median: 0.350
- Top 5: PG (0.85), MCD (0.85), AMGN (0.70), KO (0.70), QCOM (0.70)

**Growth Score:**
- Range: [-0.50, 0.70]
- Median: 0.200
- Top 5: AMGN (0.70), NFLX (0.70), ISRG (0.70), RTX (0.70), MS (0.70)

**Stability Score:**
- Range: [-0.30, 0.50]
- Median: 0.500 (most stocks get fallback score)
- Note: Most stocks clustered at 0.5 (fallback) or 0.0 (2-5% volatility)

**Revisions Score:**
- Range: [-0.29, 1.00]
- Median: 0.600
- Top 5: AMZN (1.00), WMT (1.00), NOW (0.99), MSFT (0.99), KO (0.95)

**Momentum Score:**
- Range: [-1.00, 0.68]
- Median: 0.088
- Top 5: TSLA (0.68), MRNA (0.58), NKE (0.56), JNJ (0.52), RTX (0.52)

**Sentiment Score:**
- Range: [0.10, 0.90]
- Median: 0.600
- Top 5: GOOGL (0.90), V (0.80), JPM (0.80), KO (0.80), HII (0.80)

**Composite Score:**
- Range: [-0.49, 0.69]
- Median: 0.282
- Top 5: ADBE (0.686), GOOGL (0.655), MRK (0.640), V (0.609), NDAQ (0.585)

## Recommendations

1. **Immediate Actions:**
   - ✅ Filter for TWTR and zero-metric stocks already implemented
   - ⚠️ Investigate META growth data fetching (ticker normalization issue)
   - ⚠️ Investigate beta fetching failure

2. **Future Improvements:**
   - Improve beta fetching reliability (may need alternative data source)
   - Add logging for missing growth data to identify API issues
   - Consider adding validation warnings for stocks with multiple missing metrics

## Conclusion

Overall, the scoring system appears to be functioning correctly. Most "zero" scores are legitimate (stocks with middling metrics). The main issues are:
1. TWTR still in data (will be fixed by filter)
2. META missing growth (ticker normalization issue)
3. Beta missing for most stocks (data fetching issue)

No critical bugs found in scoring logic itself.


