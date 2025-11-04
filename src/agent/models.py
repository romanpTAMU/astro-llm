from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class Candidate(BaseModel):
    ticker: str = Field(..., description="US-listed common stock ticker (uppercase)")
    sector: Optional[str] = Field(None, description="GICS sector if known")
    rationale: str = Field(..., description="One-line rationale")
    theme: Optional[str] = Field(None, description="Market theme this candidate aligns with")


class Theme(BaseModel):
    name: str = Field(..., description="Theme name")
    description: str = Field(..., description="Brief description of the theme and why it matters")
    timeframe: str = Field(..., description="Expected timeframe/relevance (e.g., 'next 6-12 months')")


class ThemeResponse(BaseModel):
    themes: List[Theme] = Field(default_factory=list)


class CandidateResponse(BaseModel):
    candidates: List[Candidate] = Field(default_factory=list)


# Phase 2: Data Models
class PriceData(BaseModel):
    ticker: str
    price: float = Field(..., description="Current/latest price")
    volume: int = Field(..., description="Current/latest volume")
    avg_volume_30d: Optional[int] = Field(None, description="30-day average volume")
    market_cap: Optional[float] = Field(None, description="Market capitalization in USD")
    price_change_pct: Optional[float] = Field(None, description="Price change % (1d, 5d, etc.)")
    as_of: Optional[datetime] = Field(None, description="Data timestamp")


class Fundamentals(BaseModel):
    ticker: str
    revenue_ttm: Optional[float] = Field(None, description="TTM revenue in USD")
    revenue_yoy_growth: Optional[float] = Field(None, description="Revenue YoY growth %")
    operating_margin_ttm: Optional[float] = Field(None, description="Operating margin TTM")
    fcf_margin_ttm: Optional[float] = Field(None, description="FCF margin TTM")
    roic: Optional[float] = Field(None, description="Return on invested capital")
    net_debt_to_ebitda: Optional[float] = Field(None, description="Net debt to EBITDA")
    pe_ratio: Optional[float] = Field(None, description="P/E ratio")
    ev_ebitda: Optional[float] = Field(None, description="EV/EBITDA")
    as_of: Optional[date] = Field(None, description="Data date")


class AnalystRecommendation(BaseModel):
    ticker: str
    consensus: Optional[str] = Field(None, description="Consensus rating (Buy/Hold/Sell)")
    price_target: Optional[float] = Field(None, description="Average price target")
    price_target_high: Optional[float] = Field(None, description="High price target")
    price_target_low: Optional[float] = Field(None, description="Low price target")
    num_analysts: Optional[int] = Field(None, description="Number of analysts")
    recent_changes: Optional[List[str]] = Field(default_factory=list, description="Recent rating changes")
    as_of: Optional[date] = Field(None, description="Data date")


class NewsItem(BaseModel):
    ticker: str
    headline: str
    summary: Optional[str] = Field(None, description="Article summary")
    source: str
    url: Optional[str] = None
    published_at: Optional[datetime] = Field(None, description="Publication date")
    sentiment: Optional[str] = Field(None, description="LLM-classified sentiment (bullish/neutral/bearish)")


class StockData(BaseModel):
    """Complete data package for a single ticker"""
    ticker: str
    price_data: Optional[PriceData] = None
    fundamentals: Optional[Fundamentals] = None
    analyst_recommendations: Optional[AnalystRecommendation] = None
    news: List[NewsItem] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.now)


class StockDataResponse(BaseModel):
    """Collection of stock data for multiple tickers"""
    data: List[StockData] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.now)


# Phase 3: Scoring Models
class FactorScores(BaseModel):
    """Normalized factor scores (z-scores)"""
    value: Optional[float] = Field(None, description="Value factor z-score (EV/EBITDA, FCF yield, P/E)")
    quality: Optional[float] = Field(None, description="Quality factor z-score (ROIC, margins)")
    growth: Optional[float] = Field(None, description="Growth factor z-score (revenue/EPS growth)")
    stability: Optional[float] = Field(None, description="Stability factor z-score (volatility, drawdowns)")
    revisions: Optional[float] = Field(None, description="Revisions factor z-score (analyst EPS revisions)")


class SentimentAnalysis(BaseModel):
    """Synthesized sentiment from analyst recs and news"""
    overall_sentiment: str = Field(..., description="Overall sentiment: bullish/neutral/bearish")
    sentiment_score: float = Field(..., description="Sentiment score [-1 to 1], where 1 is most bullish")
    analyst_consensus: Optional[str] = Field(None, description="Analyst consensus rating")
    analyst_score: Optional[float] = Field(None, description="Analyst score [-1 to 1]")
    news_sentiment: Optional[str] = Field(None, description="News sentiment summary")
    news_score: Optional[float] = Field(None, description="News sentiment score [-1 to 1]")
    key_drivers: List[str] = Field(default_factory=list, description="Key positive drivers")
    key_risks: List[str] = Field(default_factory=list, description="Key risks/concerns")
    price_target_upside: Optional[float] = Field(None, description="Upside % to price target")


class RiskFlags(BaseModel):
    """Risk screening results"""
    passed_all_checks: bool = Field(..., description="Whether stock passed all risk screens")
    failed_checks: List[str] = Field(default_factory=list, description="List of failed risk checks")
    liquidity_ok: bool = Field(True, description="Meets liquidity requirements")
    price_ok: bool = Field(True, description="Price above minimum threshold")
    no_trading_halts: bool = Field(True, description="No recent trading halts detected")
    no_pending_ma: bool = Field(True, description="No pending M&A detected")
    earnings_clear: bool = Field(True, description="No earnings within 2 trading days")


class ScoredStock(BaseModel):
    """Complete scoring analysis for a single ticker"""
    ticker: str
    sector: Optional[str] = None
    theme: Optional[str] = None
    
    # Factor scores
    factor_scores: FactorScores
    
    # Sentiment
    sentiment: SentimentAnalysis
    
    # Risk flags
    risk_flags: RiskFlags
    
    # Composite score
    composite_score: float = Field(..., description="Final composite score for ranking")
    
    # Metadata
    price: Optional[float] = None
    market_cap: Optional[float] = None
    scored_at: datetime = Field(default_factory=datetime.now)


class ScoredCandidatesResponse(BaseModel):
    """Collection of scored candidates"""
    candidates: List[ScoredStock] = Field(default_factory=list)
    scored_at: datetime = Field(default_factory=datetime.now)
    stats: Optional[dict] = Field(None, description="Summary statistics")
