from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv


class AppConfig(BaseModel):
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field("gpt-4o-mini", alias="OPENAI_MODEL")
    cheap_model: str = Field("gpt-4o-mini", alias="CHEAP_MODEL", description="Cheaper model for high-volume tasks")

    # API keys for data sources
    finnhub_api_key: Optional[str] = Field(None, alias="FINNHUB_API_KEY")
    alpha_vantage_api_key: Optional[str] = Field(None, alias="ALPHA_VANTAGE_API_KEY")
    fmp_api_key: Optional[str] = Field(None, alias="FMP_API_KEY")

    portfolio_horizon_end: date = Field(..., alias="PORTFOLIO_HORIZON_END")
    candidate_count: int = Field(60, alias="CANDIDATE_COUNT")

    sector_cap: float = Field(0.25, alias="SECTOR_CAP")
    industry_cap: float = Field(0.15, alias="INDUSTRY_CAP")
    min_weight: float = Field(0.02, alias="MIN_WEIGHT")
    max_weight: float = Field(0.10, alias="MAX_WEIGHT")

    min_avg_dollar_volume: int = Field(5_000_000, alias="MIN_AVG_DOLLAR_VOLUME")
    beta_min: float = Field(0.5, alias="BETA_MIN")
    beta_max: float = Field(1.5, alias="BETA_MAX")

    # Backtest mode
    backtest_mode: bool = Field(False, alias="BACKTEST_MODE")
    backtest_date: Optional[date] = Field(None, alias="BACKTEST_DATE", description="Point-in-time date for backtest (YYYY-MM-DD)")
    backtest_model_cutoff: Optional[date] = Field(None, alias="BACKTEST_MODEL_CUTOFF", description="Model training cutoff to simulate (YYYY-MM-DD)")

    @property
    def remaining_days(self) -> int:
        # In backtest mode, use backtest_date as "today"
        today = self.backtest_date if self.backtest_mode and self.backtest_date else date.today()
        return max(0, (self.portfolio_horizon_end - today).days)
    
    @property
    def effective_date(self) -> date:
        """Get the effective 'today' date (backtest date or actual today)."""
        return self.backtest_date if self.backtest_mode and self.backtest_date else date.today()


def load_config(dotenv: bool = True) -> AppConfig:
    if dotenv:
        load_dotenv(override=False)
    env = {k: v for k, v in os.environ.items()}

    if "PORTFOLIO_HORIZON_END" not in env:
        env["PORTFOLIO_HORIZON_END"] = "2026-05-15"

    cfg = AppConfig.model_validate(env)
    if isinstance(cfg.portfolio_horizon_end, str):
        cfg.portfolio_horizon_end = datetime.strptime(cfg.portfolio_horizon_end, "%Y-%m-%d").date()
    
    # Parse backtest_date if provided
    if isinstance(cfg.backtest_date, str):
        cfg.backtest_date = datetime.strptime(cfg.backtest_date, "%Y-%m-%d").date()
    
    # Parse backtest_model_cutoff if provided
    if isinstance(cfg.backtest_model_cutoff, str):
        cfg.backtest_model_cutoff = datetime.strptime(cfg.backtest_model_cutoff, "%Y-%m-%d").date()
    
    return cfg
