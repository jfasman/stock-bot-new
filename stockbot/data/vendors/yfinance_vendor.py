from __future__ import annotations

from typing import Optional

import pandas as pd

from .base import DataVendor
from .registry import register_vendor

try:
    import yfinance as yf
except ImportError:
    yf = None


class YFinanceVendor(DataVendor):
    name = "yfinance"

    def history(self, ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        if yf is None:
            return pd.DataFrame()
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        return df.rename(columns=str.title)

    def last_price(self, ticker: str) -> Optional[float]:
        df = self.history(ticker, period="5d", interval="1d")
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])

    def fundamentals(self, ticker: str) -> dict:
        if yf is None:
            return {}
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            return {}
        # Normalize the keys we care about; downstream factor library reads these.
        return {
            "ticker": ticker.upper(),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "trailing_pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "price_to_book": info.get("priceToBook"),
            "ev_to_ebitda": info.get("enterpriseToEbitda"),
            "fcf_yield": _safe_div(info.get("freeCashflow"), info.get("marketCap")),
            "gross_margin": info.get("grossMargins"),
            "operating_margin": info.get("operatingMargins"),
            "profit_margin": info.get("profitMargins"),
            "return_on_equity": info.get("returnOnEquity"),
            "return_on_assets": info.get("returnOnAssets"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "beta": info.get("beta"),
            "short_percent_of_float": info.get("shortPercentOfFloat"),
            "shares_short": info.get("sharesShort"),
            "earnings_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
        }

    def corporate_actions(self, ticker: str) -> pd.DataFrame:
        if yf is None:
            return pd.DataFrame(columns=["date", "type", "value"])
        try:
            t = yf.Ticker(ticker)
            rows = []
            divs = t.dividends
            for ts, val in divs.items():
                rows.append({"date": str(ts.date()), "type": "dividend", "value": float(val)})
            splits = t.splits
            for ts, val in splits.items():
                rows.append({"date": str(ts.date()), "type": "split", "value": float(val)})
            return pd.DataFrame(rows).sort_values("date") if rows else pd.DataFrame(columns=["date", "type", "value"])
        except Exception:
            return pd.DataFrame(columns=["date", "type", "value"])

    def is_delisted(self, ticker: str) -> bool:
        # yfinance does not expose this reliably; treat empty history as a soft proxy.
        df = self.history(ticker, period="5d", interval="1d")
        return df.empty

    def supports_point_in_time(self) -> bool:
        # yfinance returns already-adjusted (or restated) data with no PIT API.
        return False


def _safe_div(num, den) -> Optional[float]:
    try:
        if num is None or den is None or float(den) == 0:
            return None
        return float(num) / float(den)
    except (TypeError, ValueError):
        return None


register_vendor("yfinance", YFinanceVendor)
