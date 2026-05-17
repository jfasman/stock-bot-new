from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from .base import DataVendor, VendorError
from .registry import register_vendor


class AlphaVantageVendor(DataVendor):
    """Stub for AlphaVantage. Set ALPHAVANTAGE_API_KEY to enable."""

    name = "alphavantage"

    def __init__(self) -> None:
        self.api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if not self.api_key:
            raise VendorError("ALPHAVANTAGE_API_KEY not set.")

    def history(self, ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        raise NotImplementedError("AlphaVantage history fetch not wired yet.")

    def last_price(self, ticker: str) -> Optional[float]:
        raise NotImplementedError("AlphaVantage last price not wired yet.")


register_vendor("alphavantage", AlphaVantageVendor)
