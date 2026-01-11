# Financial Ratios Computation Summary

## Overview
This document explains how each financial ratio is computed in our system.

---

## 1. **P/E Ratio** (Price-to-Earnings)
**Source:** FMP Ratios API (`/api/v3/ratios/{ticker}`)  
**Field:** `priceEarningsRatio`  
**Method:** Direct API fetch  
**Code Location:** `src/agent/data_apis.py:822`

```python
pe_ratio = ratios_data.get("priceEarningsRatio") if ratios_data else None
```

**Notes:**
- Fetched directly from ratios endpoint
- No calculation needed
- Used in value scoring

---

## 2. **EV/EBITDA** (Enterprise Value to EBITDA)
**Source:** Tiered approach (3 methods)  
**Code Location:** `src/agent/data_apis.py:823-900`

### Method 1 (Primary): Key Metrics API
- **Endpoint:** `/api/v3/key-metrics-ttm/{ticker}`
- **Field:** `evToEBITDA`
- **Preferred source** - most reliable

### Method 2 (Fallback): Ratios API
- **Endpoint:** `/api/v3/ratios/{ticker}`
- **Field:** `enterpriseValueMultiple`
- **Issue:** Often returns `0` (invalid), so treated as missing

### Method 3 (Fallback): Manual Calculation
- **EV Source:** `/stable/enterprise-values?symbol={ticker}` → `enterpriseValue`
- **EBITDA Source:** Income statement (already fetched) → `ebitda`
- **Calculation:** `EV / EBITDA`

### Method 4 (Last Resort): Penalty Value
- If all methods fail, set to `30.0` (high = bad for value scoring)
- Warning logged

**Notes:**
- Treats `0` as invalid (data quality issue)
- Used in value scoring

---

## 3. **Operating Margin**
**Source:** Calculated from Income Statement  
**Code Location:** `src/agent/data_apis.py:746-750`

**Calculation:**
```python
operating_margin = (operating_income / revenue_ttm) * 100
```

**Data Sources:**
- `operatingIncome` from income statement
- `revenue` from income statement (TTM)

**Notes:**
- Expressed as percentage
- Used in quality scoring

---

## 4. **FCF Margin** (Free Cash Flow Margin)
**Source:** Calculated from Cash Flow Statement + Income Statement  
**Code Location:** `src/agent/data_apis.py:755-819`

**Calculation:**
```python
fcf_margin = (free_cash_flow / revenue_ttm) * 100
```

**Data Sources:**
- `freeCashFlow` from cash flow statement
- `revenue` from income statement (TTM)
- Statements matched by date for consistency

**Notes:**
- Expressed as percentage
- Used in value scoring (proxy for FCF yield)
- Optional metric (errors are silently ignored)

---

## 5. **ROIC** (Return on Invested Capital)
**Source:** FMP Key Metrics API (`/api/v3/key-metrics-ttm/{ticker}`)  
**Field:** `roicTTM`  
**Code Location:** `src/agent/data_apis.py:848-851`

**Processing:**
```python
roic_ttm = km_data.get("roicTTM")  # Already a decimal (0.5569 = 55.69%)
roic = roic_ttm * 100  # Convert to percentage
```

**Notes:**
- API returns decimal (0.5569 = 55.69%)
- Converted to percentage for consistency
- Used in quality scoring

---

## 6. **Net Debt to EBITDA**
**Source:** FMP Key Metrics API (`/api/v3/key-metrics-ttm/{ticker}`)  
**Field:** `netDebtToEBITDATTM`  
**Code Location:** `src/agent/data_apis.py:853`

```python
net_debt_to_ebitda = km_data.get("netDebtToEBITDATTM")
```

**Notes:**
- Already a ratio (no conversion needed)
- Used in quality scoring (lower is better)

---

## 7. **Revenue YoY Growth**
**Source:** Calculated from Income Statement (comparing periods)  
**Code Location:** `src/agent/data_apis.py:739-744`

**Calculation:**
```python
revenue_yoy_growth = ((revenue_ttm - prev_revenue) / prev_revenue) * 100
```

**Data Sources:**
- `revenue` from latest income statement
- `revenue` from previous period income statement

**Notes:**
- Expressed as percentage
- Used in growth scoring

---

## Summary Table

| Ratio | Source | Method | Used In |
|-------|--------|--------|---------|
| P/E Ratio | Ratios API | Direct fetch | Value Score |
| EV/EBITDA | Key Metrics API → Ratios API → Manual Calc | Tiered fetch | Value Score |
| Operating Margin | Income Statement | Calculated | Quality Score |
| FCF Margin | Cash Flow + Income Statement | Calculated | Value Score |
| ROIC | Key Metrics API | Direct fetch (converted) | Quality Score |
| Net Debt/EBITDA | Key Metrics API | Direct fetch | Quality Score |
| Revenue Growth | Income Statement (2 periods) | Calculated | Growth Score |

---

## API Endpoints Used

1. **Income Statement:** `/api/v3/income-statement/{ticker}`
2. **Cash Flow Statement:** `/api/v3/cash-flow-statement/{ticker}`
3. **Ratios:** `/api/v3/ratios/{ticker}`
4. **Key Metrics TTM:** `/api/v3/key-metrics-ttm/{ticker}`
5. **Enterprise Values:** `/stable/enterprise-values?symbol={ticker}`

---

## Data Quality Issues

1. **Ratios API `enterpriseValueMultiple`:** Often returns `0` (invalid)
   - **Fix:** Use key-metrics API as primary source
   - **Fallback:** Manual calculation from EV and EBITDA

2. **Missing Data:** Some ratios may be `None` if:
   - API doesn't have data for the ticker
   - Calculation components are missing
   - API errors occur

3. **Date Matching:** For backtesting, statements are matched by date to ensure consistency


